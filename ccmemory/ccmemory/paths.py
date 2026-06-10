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

# .gitignore dropped inside every .ccmemory/ store. The .md files are the
# git-friendly source of truth; the SQLite index is a derived cache and must
# stay out of git. ._* covers macOS AppleDouble sidecars: some filesystems
# (NFS/SMB, certain bind mounts) can't store extended attributes natively, so
# macOS materializes ._<name> files next to anything written here — including
# the index DB and even this .gitignore. Excluding them keeps commits clean.
GITIGNORE_CONTENT = """\
# ccmemory: SQLite index is a derived cache, regenerated locally.
index.db
index.db-journal
index.db-wal
index.db-shm
# legacy pre-0.6.1 index name (auto-deleted on next run, ignored meanwhile).
.memory_index.db

# macOS AppleDouble sidecars + Finder droppings (see paths.py for why).
._*
.DS_Store
"""

# Patterns we guarantee are present even in a pre-existing .gitignore (e.g. one
# written by an older ccmemory that predates ._* coverage or used the old
# .memory_index.db name). The legacy db patterns stay listed so a lingering
# pre-0.6.1 cache that hasn't been cleaned yet still can't leak into git.
GITIGNORE_REQUIRED = (
    "index.db",
    "index.db-journal",
    "index.db-wal",
    "index.db-shm",
    ".memory_index.db",
    "._*",
    ".DS_Store",
)


def ensure_gitignore(memory_dir: Path) -> bool:
    """Ensure ``<memory_dir>/.gitignore`` excludes the derived index cache and
    macOS sidecar droppings.

    Idempotent and non-destructive: writes the full template when the file is
    missing, otherwise appends only the required patterns not already present
    (never clobbers user-added lines). Returns True if it created or modified
    the file, else False. Called from every store-dir resolution so each
    project ccmemory touches self-heals — no per-project manual step.
    """
    gi = memory_dir / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE_CONTENT, encoding="utf-8")
        return True

    existing = gi.read_text(encoding="utf-8")
    present = {line.strip() for line in existing.splitlines()}
    missing = [pat for pat in GITIGNORE_REQUIRED if pat not in present]
    if not missing:
        return False

    sep = "" if existing.endswith("\n") else "\n"
    addition = sep + "\n# ccmemory: required ignore patterns\n" + "\n".join(missing) + "\n"
    gi.write_text(existing + addition, encoding="utf-8")
    return True


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
