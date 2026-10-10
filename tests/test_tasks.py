import pytest

from openbrowser.db import connect
from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime
from openbrowser.tasks import TaskStore

pytestmark = pytest.mark.anyio


def store(tmp_path, max_open_tasks=50) -> TaskStore:
    return TaskStore(connect(tmp_path / "db"), max_open_tasks)


def test_fifo_per_profile(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    b = s.spawn("p", "second")
    c = s.spawn("other", "independent")
    assert (a.state, b.state, c.state) == ("running", "queued", "running")
    assert s.position(b) == 1
    s.close(a.id)
    assert s.get(b.id).state == "running"


def test_waiting_task_keeps_its_profile(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    s.set_state(a.id, "waiting_approval", expected="running")
    b = s.spawn("p", "second")
    assert s.get(b.id).state == "queued"


def test_idle_task_is_closed_and_next_runs(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    b = s.spawn("p", "second")
    assert s.close_idle(idle_seconds=-1) == [a.id]
    assert s.get(b.id).state == "running"


def test_task_waiting_on_a_human_is_not_closed_as_idle(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    s.set_state(a.id, "human_control", expected="running")
    assert s.close_idle(idle_seconds=-1) == []


def test_restart_closes_open_tasks(tmp_path):
    db = connect(tmp_path / "db")
    a = TaskStore(db, 50).spawn("p", "first")
    assert TaskStore(db, 50).get(a.id).state == "closed"


def test_steer_notes_are_delivered_once(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    s.steer(a.id, "use the work account")
    assert s.take_notes(a.id) == ["use the work account"]
    assert s.take_notes(a.id) == []


def test_open_task_limit(tmp_path):
    s = store(tmp_path, max_open_tasks=1)
    s.spawn("p", "first")
    with pytest.raises(OpenBrowserError, match="Too many open tasks"):
        s.spawn("q", "second")


def test_live_token_is_checked(tmp_path):
    s = store(tmp_path)
    a = s.spawn("p", "first")
    assert s.by_live_token(a.id, a.live_token) == a
    with pytest.raises(OpenBrowserError, match="Invalid live view token"):
        s.by_live_token(a.id, "wrong")


async def test_queued_task_cannot_use_the_browser(config, site):
    async with Runtime(config) as rt:
        rt.tasks.spawn("p", "first")
        second = rt.tasks.spawn("p", "second")
        with pytest.raises(OpenBrowserError, match="queued: 1 task"):
            await rt.tools.navigate("test", second.id, site.url + "/login")


async def test_profile_limit(config):
    import dataclasses

    async with Runtime(dataclasses.replace(config, max_profiles=1)) as rt:
        task = rt.tasks.spawn("one", "x")
        await rt.tools.snapshot("test", task.id)
        with pytest.raises(OpenBrowserError, match="Profile limit reached"):
            rt.profiles.allow("two")
