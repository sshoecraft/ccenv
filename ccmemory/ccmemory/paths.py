"""Project root + memory dir resolution.

Single resolver shared by store, hooks, MCP server, and CLI so behavior is
identical everywhere. Replaces the scattered `_autodetect_memory_dir` /
`_default_memory_dir` functions that used to live in cli.py and hooks.py.

Resolution priorities for project root:
  1. ``CCMEMORY_PROJECT_ROOT`` env var (explicit override)
  2. Walk up from CWD looking for ``.git/``
  3. Walk up from CWD looking for ``pyproject.toml``,
     ``package.json``, ``Makefile``, ``Cargo.toml``, ``go.mod``
  4. Fall back to CWD itself

Resolution priorities for memory dir:
  1. ``CCMEMORY_DIR`` env var (explicit override)
  2. ``<project_root>/.ccmemory/`` — project-local, travels with the repo
  3. ``~/.claude/projects/<cwd-slug>/memory/`` — legacy Claude Code location,
     for back-compat with un-migrated projects
  4. None (caller must handle)
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_LOCAL_DIRNAME = ".ccmemory"

# Project markers checked when there's no .git/. Order matters only insofar
# as the first match wins per directory level.
PROJECT_MARKERS = ("pyproject.toml", "package.json", "Makefile", "Cargo.toml", "go.mod")


def project_root(start: Path | None = None) -> Path | None:
    """Find the project root by walking up from start (default CWD).

    Returns None only if no marker is found anywhere up to /.
    """
    env = os.environ.get("CCMEMORY_PROJECT_ROOT")
    if env:
        p = Path(env).resolve()
        return p if p.exists() else None

    cur = (start or Path.cwd()).resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        for marker in PROJECT_MARKERS:
            if (cur / marker).exists():
                return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def project_memory_dir(start: Path | None = None) -> Path | None:
    """``<project_root>/.ccmemory/`` if a project root resolves, else None."""
    root = project_root(start)
    if not root:
        return None
    return root / PROJECT_LOCAL_DIRNAME


def legacy_memory_dir(start: Path | None = None) -> Path | None:
    """Claude Code's per-project memory location (pre-ccmemory-v0.6).

    ``~/.claude/projects/<cwd-slug>/memory/`` where slug is the absolute
    path with slashes replaced by dashes. Returns the path even if it
    doesn't exist (callers decide what to do with that).
    """
    cwd = (start or Path.cwd()).resolve()
    slug = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


def resolve_memory_dir(start: Path | None = None, must_exist: bool = True) -> Path | None:
    """The canonical memory dir for this cwd.

    Order:
      1. CCMEMORY_DIR env var
      2. Project-local .ccmemory/ if it exists
      3. Legacy ~/.claude/projects/<slug>/memory/ if it exists
      4. None (or the project-local path if must_exist=False)
    """
    env = os.environ.get("CCMEMORY_DIR")
    if env:
        p = Path(env)
        if not must_exist or p.exists():
            return p

    proj = project_memory_dir(start)
    if proj and proj.exists():
        return proj

    legacy = legacy_memory_dir(start)
    if legacy and legacy.exists():
        return legacy

    # When must_exist=False, return the *preferred* future location so
    # callers (e.g. migrate, memory_write into a fresh project) have
    # something to create.
    if not must_exist and proj:
        return proj
    return None
