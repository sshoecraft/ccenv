"""Processes by /proc: whether one is still the same process, and its whole tree.

A pid alone is not an identity: pids are reused. A process is pinned by its pid
and its start time, and is only ever signalled while both still match.
"""
from __future__ import annotations

import os
from pathlib import Path


def stat_fields(pid: int) -> list[bytes] | None:
    """Fields of /proc/<pid>/stat after the command name, or None once it is gone."""
    try:
        data = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    end = data.rfind(b")")
    return data[end + 2:].split() if end >= 0 else None


def identity(pid: int) -> int | None:
    """The process's start time, so a pid that has been reused is never signalled."""
    fields = stat_fields(pid)
    return int(fields[19]) if fields and len(fields) > 19 else None


def alive(pid: int | None, start: int | None) -> bool:
    """Whether the process recorded as (pid, start time) is still running. A zombie
    has finished; only its parent has not collected it yet."""
    if not pid:
        return False
    fields = stat_fields(pid)
    if not fields or len(fields) <= 19 or fields[0] == b"Z":
        return False
    return start is None or int(fields[19]) == start


def tree(pid: int) -> list[tuple[int, int]]:
    """`pid` and every live descendant, deepest first, as (pid, start time). ccloop
    starts claude in a session of its own, so the worker's process group alone
    misses claude and every tool it is running."""
    children: dict[int, list[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        names = []
    for name in names:
        if name.isdigit():
            fields = stat_fields(int(name))
            if fields and len(fields) > 1:
                children.setdefault(int(fields[1]), []).append(int(name))
    found: list[tuple[int, int]] = []

    def walk(parent: int) -> None:
        for child in children.get(parent, ()):
            walk(child)
            start = identity(child)
            if start is not None:
                found.append((child, start))

    walk(pid)
    start = identity(pid)
    if start is not None:
        found.append((pid, start))
    return found


def signal_tree(victims: list[tuple[int, int]], sig: int) -> None:
    for pid, start in victims:
        if identity(pid) != start:
            continue
        try:
            os.kill(pid, sig)
        except OSError:
            pass
