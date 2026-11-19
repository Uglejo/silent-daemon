from __future__ import annotations

import re

BLOCK = re.compile(r"^<{5,9} ?SEARCH[ \t]*\n(.*?)^={5,9}[ \t]*\n(.*?)^>{5,9} ?REPLACE[ \t]*$", re.S | re.M)


def edit_blocks(reply: str) -> list[tuple[str, str]]:
    return BLOCK.findall(reply.replace("\r\n", "\n"))


def replaced(source: str, search: str, replacement: str) -> str | None:
    if not search.strip():
        return None
    count = source.count(search)
    if count == 1:
        return source.replace(search, replacement, 1)
    if count > 1:
        return None
    lines = source.split("\n")
    bare = [line.rstrip() for line in lines]
    wanted = [line.rstrip() for line in search.rstrip("\n").split("\n")]
    hits = [at for at in range(len(lines) - len(wanted) + 1) if bare[at:at + len(wanted)] == wanted]
    if len(hits) != 1:
        return None
    new = replacement.rstrip("\n").split("\n") if replacement.strip() else []
    return "\n".join(lines[:hits[0]] + new + lines[hits[0] + len(wanted):])


def edited(source: str, reply: str) -> tuple[str | None, int, int]:
    blocks = edit_blocks(reply)
    applied = 0
    for search, replacement in blocks:
        result = replaced(source, search, replacement)
        if result is not None:
            source, applied = result, applied + 1
    return (source if applied else None), applied, len(blocks) - applied
