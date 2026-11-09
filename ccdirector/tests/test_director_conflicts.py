"""ccteam configured beside ccdirector is found wherever Claude Code keeps it,
and `ccdirector start`, and the migration of state left under `.ccteam/director/`."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from ccdirector import conflicts


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_nothing_configured(tmp_path):
    home, root = tmp_path / "home", tmp_path / "proj"
    root.mkdir()
    write(home / ".claude.json", {"mcpServers": {"ccmemory": {}}})
    write(home / ".claude" / "plugins" / "installed_plugins.json",
          {"plugins": {"ralph-loop@claude-plugins-official": []}})
    assert conflicts.ccteam(root, home) == []


def test_every_place_ccteam_can_be_configured(tmp_path):
    home, root = tmp_path / "home", tmp_path / "proj"
    root.mkdir()
    write(home / ".claude.json", {"mcpServers": {"ccteam": {}},
                                  "projects": {str(root): {"mcpServers": {"ccteam": {}}}}})
    write(root / ".mcp.json", {"mcpServers": {"ccteam": {"command": "ccteam-mcp"}}})
    write(home / ".claude" / "plugins" / "installed_plugins.json",
          {"plugins": {"ccteam@local": []}})
    write(home / ".claude" / "settings.json", {
        "enabledPlugins": {"ccteam@local": True, "other@x": True},
        "hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [
            {"type": "command", "command": "/home/u/.local/bin/ccteam hook pre"}]}]}})
    found = conflicts.ccteam(root, home)
    assert len(found) == 6, found
    assert any("user scope" in f for f in found) and any(".mcp.json" in f for f in found)
    assert any("enabled" in f for f in found) and any("hook" in f for f in found)


def test_a_disabled_plugin_is_not_enabled(tmp_path):
    home, root = tmp_path / "home", tmp_path / "proj"
    root.mkdir()
    write(home / ".claude" / "settings.json", {"enabledPlugins": {"ccteam@local": False}})
    assert conflicts.ccteam(root, home) == []


def test_start_refuses_beside_ccteam(tmp_path):
    home, root = tmp_path / "home", tmp_path / "proj"
    root.mkdir()
    write(root / ".mcp.json", {"mcpServers": {"ccteam": {}}})
    env = dict(os.environ, HOME=str(home))
    done = subprocess.run([sys.executable, "-m", "ccdirector.cli", "start", str(root),
                           "--goal", "anything"], capture_output=True, text=True, env=env)
    assert done.returncode == 1 and "ccteam is configured here" in done.stderr
    assert not (root / ".ccdirector").exists(), "refused before writing anything"


def test_state_from_inside_ccteam_moves_to_ccdirector(tmp_path):
    old = tmp_path / ".ccteam" / "director"
    old.mkdir(parents=True)
    (old / "goal.json").write_text("{}")
    done = subprocess.run([sys.executable, "-m", "ccdirector.cli", "status", str(tmp_path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / ".ccdirector" / "goal.json").is_file() and not old.exists()
    assert "moved" in done.stderr
