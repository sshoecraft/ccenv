"""Startup dir + memory dir resolution.

Single resolver shared by store, hooks, MCP server, and CLI so behavior is
identical everywhere. Replaces the scattered `_autodetect_memory_dir` /
`_default_memory_dir` functions that used to live in cli.py and hooks.py.

The anchor is the directory Claude Code was started in (CWD) — full stop.
ccmemory NEVER walks up the tree, NEVER hunts for ``.git/`` or build-system
markers, and reads NO environment variables to relocate the store. Memory
belongs to the exact directory the session started in. So an autonomous
ccloop run dir (no ``.git``, no build files) gets its own store right where it
runs, and a session started in a subdirectory keeps its memories local to that
subdirectory — re-launching there later finds them, and they never leak up to
a parent they don't belong to.

Startup dir:
  - CWD. Nothing else.

Memory dir:
  1. ``<startup_dir>/.ccmemory/`` — i.e. ``<cwd>/.ccmemory/``, travels with the dir
  2. ``~/.claude/projects/<cwd-slug>/memory/`` — legacy Claude Code location,
     read-only fallback for un-migrated projects (the source the MCP server
     auto-copies into ``.ccmemory/`` on first boot)
"""

from __future__ import annotations

from pathlib import Path

PROJECT_LOCAL_DIRNAME = ".ccmemory"

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


def startup_dir(start: Path | None = None) -> Path:
    """The directory Claude Code was started in (CWD) — full stop.

    ccmemory does NOT walk up the tree, does NOT hunt for ``.git/`` or
    build-system markers, and reads NO environment variable to relocate the
    anchor. It is exactly the directory the session started in, so a
    ccloop/autonomous run dir gets its own ``.ccmemory/`` where it runs, and a
    subdirectory session keeps its memories local to that subdir.
    """
    return (start or Path.cwd()).resolve()


def startup_memory_dir(start: Path | None = None) -> Path:
    """``<startup_dir>/.ccmemory/`` — the store for the dir CC was started in."""
    return startup_dir(start) / PROJECT_LOCAL_DIRNAME


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
    """The canonical memory dir for the startup (CWD) directory.

    Order:
      1. ``<startup_dir>/.ccmemory/`` if it exists
      2. Legacy ``~/.claude/projects/<slug>/memory/`` if it exists (read-only
         back-compat for un-migrated projects)
      3. With ``must_exist=False``, the ``<startup_dir>/.ccmemory/`` path so
         callers (memory_write, migrate) have somewhere to create; else None.
    """
    store = startup_memory_dir(start)
    if store.exists():
        return store

    legacy = legacy_memory_dir(start)
    if legacy and legacy.exists():
        return legacy

    # must_exist=False: hand back the preferred location to create.
    if not must_exist:
        return store
    return None
