"""Register/unregister ccprospect hooks in Claude Code's settings.json.

Same machinery as ccmemory/installer.py — atomic writes with timestamped
backups, idempotent registration, self-healing for relocated executables,
clean uninstall that doesn't clobber foreign hooks.

ccprospect owns three hook slots:

- ``SessionStart``                     → ``ccprospect hook session``
  : evaluate open predicates + inject the inbox (the wake boundary)
- ``Stop``                             → ``ccprospect hook stop``
  : regenerate PROSPECT.md
- ``PreToolUse`` (Write|Edit|NotebookEdit) → ``ccprospect hook guard``
  : block hand edits to contracts/, events.jsonl, PROSPECT.md

Autoinstalled on MCP server boot (the real entry point). Set
``CCPROSPECT_NO_AUTOINSTALL=1`` to skip.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

HOOKS = [
    ("SessionStart", "", "session"),
    ("Stop", "", "stop"),
    ("PreToolUse", "Write|Edit|NotebookEdit", "guard"),
]

OUR_SUBCOMMANDS = {sub for _, _, sub in HOOKS}


def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _exe(executable: str | None = None) -> str:
    return executable or os.path.realpath(sys.argv[0])


def hook_command(subcommand: str, executable: str | None = None) -> str:
    return f"{_exe(executable)} hook {subcommand}"


def _entry_commands(entry: dict) -> list[str]:
    return [h.get("command") for h in (entry.get("hooks") or []) if isinstance(h, dict)]


def _is_ours(command: str | None) -> bool:
    if not command:
        return False
    parts = command.split()
    if len(parts) < 3:
        return False
    if parts[-2] != "hook":
        return False
    if parts[-1] not in OUR_SUBCOMMANDS:
        return False
    return "ccprospect" in parts[0]


def _load(settings_path: Path) -> dict:
    p = Path(settings_path)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{settings_path} top level is not a JSON object")
    return data


def _atomic_write(settings_path: Path, data: dict):
    p = Path(settings_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        backup = f"{p}.bak.{time.strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(p, backup)
    tmp = f"{p}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)


def _ensure_event(data: dict, event: str, matcher: str, command: str) -> str:
    """Place ``command`` under ``data.hooks[event]`` with the given matcher.
    Self-heals stale ours-entries (relocated executable). Returns
    ``"present"``/``"added"``/``"updated"``."""
    hooks = data.setdefault("hooks", {})
    entries = hooks.get(event) or []

    had_exact = False
    had_stale = False
    rebuilt = []
    for entry in entries:
        cmds = _entry_commands(entry)
        entry_matcher = entry.get("matcher", "")
        if command in cmds and entry_matcher == matcher:
            had_exact = True
        ours = [c for c in cmds if _is_ours(c)]
        if not ours:
            rebuilt.append(entry)
            continue
        same_subcommand = any(c.split()[-1] == command.split()[-1] for c in ours)
        if not same_subcommand:
            rebuilt.append(entry)
            continue
        kept_hooks = [
            h for h in (entry.get("hooks") or [])
            if not (isinstance(h, dict) and _is_ours(h.get("command"))
                    and h.get("command", "").split()[-1] == command.split()[-1])
        ]
        if any(c != command for c in ours):
            had_stale = True
        if kept_hooks:
            entry = dict(entry)
            entry["hooks"] = kept_hooks
            rebuilt.append(entry)

    if had_exact and not had_stale:
        hooks[event] = entries
        return "present"

    new_entry = ({"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
                 if matcher else {"hooks": [{"type": "command", "command": command}]})
    rebuilt.append(new_entry)
    hooks[event] = rebuilt
    return "updated" if had_stale else "added"


def ensure_registered(settings_path: Path | None = None, executable: str | None = None) -> str:
    settings_path = settings_path or default_settings_path()
    data = _load(settings_path)

    rank = {"present": 0, "added": 1, "updated": 2}
    worst = "present"
    for event, matcher, sub in HOOKS:
        cmd = hook_command(sub, executable)
        status = _ensure_event(data, event, matcher, cmd)
        if rank[status] > rank[worst]:
            worst = status

    if worst != "present":
        _atomic_write(settings_path, data)
    return worst


def is_registered(settings_path: Path | None = None) -> bool:
    settings_path = settings_path or default_settings_path()
    try:
        data = _load(settings_path)
    except (ValueError, json.JSONDecodeError):
        return False
    found = set()
    for event, _matcher, _sub in HOOKS:
        for entry in (data.get("hooks") or {}).get(event) or []:
            for c in _entry_commands(entry):
                if _is_ours(c):
                    found.add(c.split()[-1])
    return found >= OUR_SUBCOMMANDS


def uninstall(settings_path: Path | None = None) -> bool:
    settings_path = settings_path or default_settings_path()
    try:
        data = _load(settings_path)
    except (ValueError, json.JSONDecodeError):
        return False
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        entries = hooks.get(event) or []
        rebuilt = []
        ev_changed = False
        for entry in entries:
            kept = [
                h for h in (entry.get("hooks") or [])
                if not (isinstance(h, dict) and _is_ours(h.get("command")))
            ]
            if len(kept) != len(entry.get("hooks") or []):
                ev_changed = True
                changed = True
            if kept:
                entry = dict(entry)
                entry["hooks"] = kept
                rebuilt.append(entry)
        if ev_changed:
            if rebuilt:
                hooks[event] = rebuilt
            else:
                hooks.pop(event, None)
    if not changed:
        return False
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _atomic_write(settings_path, data)
    return True


def autoinstall_quiet():
    """Idempotent first-run install. Called on MCP boot. Fail-open."""
    if os.environ.get("CCPROSPECT_NO_AUTOINSTALL"):
        return
    try:
        ensure_registered()
    except Exception as e:
        sys.stderr.write(f"[ccprospect] autoinstall skipped: {e}\n")
