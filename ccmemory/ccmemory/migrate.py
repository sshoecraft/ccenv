"""Migrate memory from Claude Code's legacy location into the project.

Auto-runs on first MCP server boot in a project that has no ``.ccmemory/``
yet but has memory in ``~/.claude/projects/<slug>/memory/``. Also exposed
as ``ccmemory migrate`` for explicit / non-auto-detected cases.

Safety rules:
  - Never delete the source. Always copy. User removes the legacy dir
    manually after verifying things work.
  - Refuse if destination already has any .md content (run an explicit
    overwrite if you need to merge).
  - SHA-256 every source file and every destination file; refuse to call
    it done unless they match 1:1.
  - Drop a ``.gitignore`` inside ``.ccmemory/`` so the SQLite cache stays
    out of git.
  - Write ``.ccmemory/.migrated-from`` marker recording the source path
    and timestamp for provenance.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths


@dataclass
class MigrationResult:
    status: str  # "ok" | "skipped" | "refused" | "no-source" | "no-target"
    source: Path | None = None
    target: Path | None = None
    files_copied: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "source": str(self.source) if self.source else None,
            "target": str(self.target) if self.target else None,
            "files_copied": self.files_copied,
            "reason": self.reason,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_md_files(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.md") if p.is_file())


def migrate(
    *,
    source: Path | None = None,
    target: Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> MigrationResult:
    """Copy .md files source → target with hash verification.

    When source/target are None, auto-resolve: source = legacy dir for cwd,
    target = ``<project_root>/.ccmemory/``.
    """
    if source is None:
        source = paths.legacy_memory_dir()
    if target is None:
        target = paths.project_memory_dir()

    if target is None:
        return MigrationResult(
            status="no-target",
            reason="no project root resolvable from cwd; pass --to or set CCMEMORY_PROJECT_ROOT",
        )

    if source is None or not source.exists():
        return MigrationResult(
            status="no-source",
            source=source,
            target=target,
            reason=f"source dir does not exist: {source}",
        )

    md_files = _list_md_files(source)
    if not md_files:
        return MigrationResult(
            status="no-source",
            source=source,
            target=target,
            reason=f"source dir has no .md files: {source}",
        )

    # Refuse if destination already has .md content unless overwrite.
    if target.exists() and _list_md_files(target) and not overwrite:
        return MigrationResult(
            status="refused",
            source=source,
            target=target,
            reason=f"destination already has .md files; rerun with --overwrite to replace: {target}",
        )

    if dry_run:
        return MigrationResult(
            status="ok",
            source=source,
            target=target,
            files_copied=len(md_files),
            reason="dry-run (no changes made)",
        )

    target.mkdir(parents=True, exist_ok=True)

    # Copy files preserving mtime, then verify hashes.
    copied = []
    for src in md_files:
        dst = target / src.name
        shutil.copy2(src, dst)
        copied.append((src, dst))

    mismatches = []
    for src, dst in copied:
        if _sha256(src) != _sha256(dst):
            mismatches.append((src, dst))
    if mismatches:
        # Roll back the copies — leave source untouched, but remove dest files
        # we just wrote.
        for _, dst in copied:
            try:
                dst.unlink()
            except OSError:
                pass
        return MigrationResult(
            status="refused",
            source=source,
            target=target,
            reason=f"hash mismatch on {len(mismatches)} file(s); rolled back",
        )

    # Drop/refresh the gitignore for the SQLite cache + macOS sidecars.
    paths.ensure_gitignore(target)

    # Provenance marker.
    marker = target / ".migrated-from"
    marker.write_text(
        f"source: {source}\nwhen:   {datetime.now(timezone.utc).isoformat()}\nfiles:  {len(copied)}\n",
        encoding="utf-8",
    )

    return MigrationResult(
        status="ok",
        source=source,
        target=target,
        files_copied=len(copied),
    )


def automigrate_quiet() -> MigrationResult | None:
    """Auto-migrate trigger called from MCP server startup.

    Fires only when:
      - CCMEMORY_NO_AUTOMIGRATE is unset
      - project root resolves
      - <project_root>/.ccmemory/ does not yet exist (or is empty)
      - legacy dir exists and has .md files

    Always copy, never move. Logs one line to stderr on success.
    """
    if os.environ.get("CCMEMORY_NO_AUTOMIGRATE"):
        return None

    target = paths.project_memory_dir()
    if not target:
        return None

    # Already migrated (or user has populated .ccmemory/ themselves) — skip.
    if target.exists() and _list_md_files(target):
        return None

    source = paths.legacy_memory_dir()
    if not source or not source.exists() or not _list_md_files(source):
        return None

    try:
        result = migrate(source=source, target=target)
    except Exception as e:
        sys.stderr.write(f"[ccmemory] auto-migrate failed (fail-open): {e}\n")
        return None

    if result.status == "ok":
        sys.stderr.write(
            f"[ccmemory] auto-migrated {result.files_copied} memories\n"
            f"           from {result.source}\n"
            f"           to   {result.target}\n"
            f"           source preserved; remove it manually after verifying\n"
        )
    return result
