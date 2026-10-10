import pytest

from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime
from openbrowser.tasks import TaskStore

pytestmark = pytest.mark.anyio


def test_fifo_per_profile(tmp_path):
    store = TaskStore(tmp_path / "db")
    a = store.spawn("p", "first")
    b = store.spawn("p", "second")
    c = store.spawn("other", "independent")
    assert (a.state, b.state, c.state) == ("running", "queued", "running")
    assert store.position(b) == 1

    store.close(a.id)
    assert store.get(b.id).state == "running"


def test_idle_task_is_closed_and_next_runs(tmp_path):
    store = TaskStore(tmp_path / "db")
    a = store.spawn("p", "first")
    b = store.spawn("p", "second")
    assert store.close_idle(idle_seconds=-1) == [a.id]
    assert store.get(b.id).state == "running"


def test_restart_closes_open_tasks(tmp_path):
    a = TaskStore(tmp_path / "db").spawn("p", "first")
    assert TaskStore(tmp_path / "db").get(a.id).state == "closed"


def test_steer_notes_are_delivered_once(tmp_path):
    store = TaskStore(tmp_path / "db")
    a = store.spawn("p", "first")
    store.steer(a.id, "use the work account")
    assert store.take_notes(a.id) == ["use the work account"]
    assert store.take_notes(a.id) == []


async def test_queued_task_cannot_use_the_browser(config, site):
    async with Runtime(config) as rt:
        rt.tasks.spawn("p", "first")
        second = rt.tasks.spawn("p", "second")
        with pytest.raises(OpenBrowserError, match="queued: 1 task"):
            await rt.tools.navigate(second.id, site.url + "/login")
