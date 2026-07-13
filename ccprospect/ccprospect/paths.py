"""Startup dir + prospect dir resolution.

Same resolver contract as ccmemory/paths.py: the anchor is the directory
Claude Code was started in (CWD) — full stop. ccprospect NEVER walks up the
tree, NEVER hunts for ``.git/`` or build-system markers, and reads NO
environment variables to relocate the store. Prospects belong to the exact
directory the session started in, so an autonomous ccloop run dir gets its
own store right where it runs and open intentions never leak up to a parent.

Store layout (travels with the repo, like ``.ccmemory/``):

    .ccprospect/
      contracts/p-0007-<slug>.md   one file per contract; IMMUTABLE after creation
      events.jsonl                 append-only event log (ALL state derives from it)
      PROSPECT.md                  GENERATED digest (like MEMORY.md) — never hand-edit
      probe_state.json             LOCAL probe watermarks + nudge counters, gitignored
      integration.json             project fact written by the prospect-integrate
                                   skill (shape + binding_file); travels with the repo
      .gitignore                   excludes the local/derived files above
"""

from __future__ import annotations

from pathlib import Path

PROJECT_LOCAL_DIRNAME = ".ccprospect"
CONTRACTS_DIRNAME = "contracts"
EVENTS_FILENAME = "events.jsonl"
INDEX_FILENAME = "PROSPECT.md"
PROBE_STATE_FILENAME = "probe_state.json"

# The contract files, events.jsonl, and PROSPECT.md are the git-friendly
# record and MUST travel with the repo (a clone carries its open intentions).
# probe_state.json is per-machine evaluation state (probe rate-limit
# watermarks, nudge counters) and must NOT — machine A probing at 10:00 says
# nothing about machine B. index.db* is reserved for a future derived SQLite
# cache (none exists in v1; state derivation is a cheap fold at these caps).
# ._* / .DS_Store: macOS AppleDouble sidecars on xattr-less filesystems.
GITIGNORE_CONTENT = """\
# ccprospect: per-machine evaluation state, never shared.
probe_state.json
# reserved for a future derived index cache.
index.db
index.db-journal
index.db-wal
index.db-shm

# macOS AppleDouble sidecars + Finder droppings.
._*
.DS_Store
"""

GITIGNORE_REQUIRED = (
    "probe_state.json",
    "index.db",
    "index.db-journal",
    "index.db-wal",
    "index.db-shm",
    "._*",
    ".DS_Store",
)


def ensure_gitignore(prospect_dir: Path) -> bool:
    """Idempotent, non-destructive .gitignore self-heal (ccmemory pattern):
    write the template when missing, else append only absent required
    patterns. Returns True if it created or modified the file."""
    gi = prospect_dir / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE_CONTENT, encoding="utf-8")
        return True

    existing = gi.read_text(encoding="utf-8")
    present = {line.strip() for line in existing.splitlines()}
    missing = [pat for pat in GITIGNORE_REQUIRED if pat not in present]
    if not missing:
        return False

    sep = "" if existing.endswith("\n") else "\n"
    addition = sep + "\n# ccprospect: required ignore patterns\n" + "\n".join(missing) + "\n"
    gi.write_text(existing + addition, encoding="utf-8")
    return True


def startup_dir(start: Path | None = None) -> Path:
    """The directory Claude Code was started in (CWD) — full stop."""
    return (start or Path.cwd()).resolve()


def startup_prospect_dir(start: Path | None = None) -> Path:
    """``<startup_dir>/.ccprospect/`` — the store for the dir CC was started in."""
    return startup_dir(start) / PROJECT_LOCAL_DIRNAME


def resolve_prospect_dir(start: Path | None = None, must_exist: bool = True) -> Path | None:
    """The canonical prospect dir for the startup (CWD) directory.

    With ``must_exist=True`` (hooks, read tools): the existing store or None —
    a project opts in by filing its first prospect, and hooks stay silent
    everywhere else. With ``must_exist=False`` (prospect_file): the path to
    create."""
    store = startup_prospect_dir(start)
    if store.exists():
        return store
    if not must_exist:
        return store
    return None


def contracts_dir(prospect_dir: Path) -> Path:
    return Path(prospect_dir) / CONTRACTS_DIRNAME


def events_path(prospect_dir: Path) -> Path:
    return Path(prospect_dir) / EVENTS_FILENAME


def index_path(prospect_dir: Path) -> Path:
    return Path(prospect_dir) / INDEX_FILENAME


def probe_state_path(prospect_dir: Path) -> Path:
    return Path(prospect_dir) / PROBE_STATE_FILENAME
