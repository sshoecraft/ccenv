"""settings.json registration: idempotent, self-healing, foreign-safe."""

from __future__ import annotations

import json

from ccprospect import installer


def read(settings):
    return json.loads(settings.read_text(encoding="utf-8"))


def test_register_adds_all_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    exe = "/home/u/.local/bin/ccprospect"
    assert installer.ensure_registered(settings, executable=exe) == "added"

    data = read(settings)
    assert any(f"{exe} hook session" in installer._entry_commands(e)
               for e in data["hooks"]["SessionStart"])
    assert any(f"{exe} hook stop" in installer._entry_commands(e)
               for e in data["hooks"]["Stop"])
    guard_entries = [e for e in data["hooks"]["PreToolUse"]
                     if f"{exe} hook guard" in installer._entry_commands(e)]
    assert guard_entries and guard_entries[0]["matcher"] == "Write|Edit|NotebookEdit"


def test_register_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    exe = "/home/u/.local/bin/ccprospect"
    installer.ensure_registered(settings, executable=exe)
    before = settings.read_text()
    assert installer.ensure_registered(settings, executable=exe) == "present"
    assert settings.read_text() == before


def test_register_heals_relocated_executable(tmp_path):
    settings = tmp_path / "settings.json"
    installer.ensure_registered(settings, executable="/old/venv/bin/ccprospect")
    assert installer.ensure_registered(
        settings, executable="/home/u/.local/bin/ccprospect") == "updated"
    text = settings.read_text()
    assert "/old/venv/bin/ccprospect" not in text
    assert "/home/u/.local/bin/ccprospect hook session" in text


def test_uninstall_preserves_foreign_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command",
                                       "command": "/usr/bin/ccmemory hook stop"}]}]},
        "model": "opus",
    }))
    exe = "/home/u/.local/bin/ccprospect"
    installer.ensure_registered(settings, executable=exe)
    assert installer.uninstall(settings) is True

    data = read(settings)
    assert data["model"] == "opus"
    stop_cmds = [c for e in data["hooks"]["Stop"] for c in installer._entry_commands(e)]
    assert stop_cmds == ["/usr/bin/ccmemory hook stop"]
    assert "SessionStart" not in data["hooks"]


def test_is_registered(tmp_path):
    settings = tmp_path / "settings.json"
    assert installer.is_registered(settings) is False
    installer.ensure_registered(settings, executable="/x/ccprospect")
    assert installer.is_registered(settings) is True


def test_is_ours_never_claims_ccmemory():
    assert installer._is_ours("/usr/bin/ccmemory hook stop") is False
    assert installer._is_ours("/usr/bin/ccprospect hook stop") is True
    assert installer._is_ours("/usr/bin/ccprospect hook nonsense") is False
