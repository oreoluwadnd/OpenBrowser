import re

CLICKABLE_ROLES = ("link", "button", "checkbox", "radio", "tab", "menuitem", "option", "switch")
# "- generic [ref=e12]:" with nothing else: a layout box with no name, text or click target.
EMPTY_WRAPPER = re.compile(r"- generic \[ref=[^\]]+\]:$")
CURSOR = " [cursor=pointer]"


def compact(tree: str) -> str:
    """Shrink a Playwright aria snapshot without losing anything the agent can act on.

    - Drops `/url:` lines. The agent clicks links by ref and sees the address after it navigates.
    - Removes empty `generic` wrappers and moves their children up a level.
    - Drops `[cursor=pointer]` on roles that are clickable anyway.
    """
    out: list[str] = []
    # Indent levels of removed wrappers that are still open; each one shifts its children left.
    removed: list[int] = []
    for line in tree.splitlines():
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        while removed and indent <= removed[-1]:
            removed.pop()
        if body.startswith("- /url:"):
            continue
        if EMPTY_WRAPPER.match(body):
            removed.append(indent)
            continue
        if CURSOR in body and body[2:].startswith(CLICKABLE_ROLES):
            body = body.replace(CURSOR, "", 1)
        out.append(" " * (indent - 2 * len(removed)) + body)
    return "\n".join(_drop_dangling_colons(out))


def _drop_dangling_colons(lines: list[str]) -> list[str]:
    """`- link "Home" [ref=e3]:` whose only child was its /url line loses the colon."""
    result = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.endswith(":") and _indent(nxt) <= _indent(line):
            line = line[:-1]
        result.append(line)
    return result


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))
