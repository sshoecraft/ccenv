"""Where ccteam is configured for a project: ccdirector refuses to run beside it.

ccteam's plugin loads in every Claude Code session, so it would run in the
director's and every worker's. In its NATS mode each session's watcher applies
the other sessions' edits to its own copy of the tree — here the same copy, so
every edit would be replayed onto a tree that already has it — and its hook
takes a second lock around every edit through one MCP server per project that
cannot tell the sessions apart.

This reads Claude Code's own configuration files and reports every place
ccteam appears; it changes nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

NAME = "ccteam"


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def hook_commands(settings: dict) -> list[str]:
    found = []
    for entries in (settings.get("hooks") or {}).values():
        for entry in entries if isinstance(entries, list) else []:
            for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                command = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(command, str) and command.split():
                    found.append(command)
    return found


def ccteam(root: Path, home: Path | None = None) -> list[str]:
    """Every place ccteam is configured for `root`; empty when there is none."""
    home = home or Path.home()
    found = []
    claude = load(home / ".claude.json")
    if NAME in (claude.get("mcpServers") or {}):
        found.append(f"MCP server '{NAME}' at user scope in {home / '.claude.json'}")
    project = (claude.get("projects") or {}).get(str(root)) or {}
    if isinstance(project, dict) and NAME in (project.get("mcpServers") or {}):
        found.append(f"MCP server '{NAME}' for {root} in {home / '.claude.json'}")
    if NAME in (load(root / ".mcp.json").get("mcpServers") or {}):
        found.append(f"MCP server '{NAME}' in {root / '.mcp.json'}")
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    for key in (load(installed).get("plugins") or {}):
        if NAME in key.lower():
            found.append(f"plugin {key} installed ({installed})")
    for settings_path in (home / ".claude" / "settings.json", root / ".claude" / "settings.json",
                          root / ".claude" / "settings.local.json"):
        settings = load(settings_path)
        for key, enabled in (settings.get("enabledPlugins") or {}).items():
            if NAME in key.lower() and enabled:
                found.append(f"plugin {key} enabled in {settings_path}")
        for command in hook_commands(settings):
            if os.path.basename(command.split()[0]) in (NAME, f"{NAME}-mcp"):
                found.append(f"hook '{command}' in {settings_path}")
    return found
