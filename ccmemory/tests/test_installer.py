"""Installer: idempotent install, foreign-hook preservation, clean uninstall."""

import json

from ccmemory import installer


def test_install_into_empty_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    status = installer.ensure_registered(settings_path=settings, executable="/bin/ccmemory")
    assert status == "added"
    data = json.loads(settings.read_text())
    assert {h["matcher"] if "matcher" in h else "" for h in data["hooks"]["PreToolUse"]} >= {"Write|Edit|NotebookEdit", "Read"}
    assert data["hooks"]["Stop"]
    assert data["hooks"]["SessionStart"]


def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    installer.ensure_registered(settings_path=settings, executable="/bin/ccmemory")
    second = installer.ensure_registered(settings_path=settings, executable="/bin/ccmemory")
    assert second == "present"


def test_install_preserves_foreign_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/foreign/hook.sh"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "/path/to/ccloop keepgoing"}]}],
        }
    }))
    installer.ensure_registered(settings_path=settings, executable="/bin/ccmemory")
    data = json.loads(settings.read_text())
    bash_entries = [e for e in data["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"]
    assert bash_entries and bash_entries[0]["hooks"][0]["command"] == "/foreign/hook.sh"
    ccloop_stop = [
        h for e in data["hooks"]["Stop"] for h in e["hooks"]
        if "ccloop" in h["command"]
    ]
    assert ccloop_stop, "ccloop Stop hook was clobbered"


def test_install_self_heals_relocated_executable(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    installer.ensure_registered(settings_path=settings, executable="/old/path/ccmemory")
    status = installer.ensure_registered(settings_path=settings, executable="/new/path/ccmemory")
    assert status == "updated"
    data = json.loads(settings.read_text())
    all_cmds = [h["command"] for e_list in data["hooks"].values() for e in e_list for h in e["hooks"]]
    assert all("/old/path" not in c for c in all_cmds)
    assert any("/new/path/ccmemory" in c for c in all_cmds)


def test_uninstall_clean(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/foreign/hook.sh"}]}]}
    }))
    installer.ensure_registered(settings_path=settings, executable="/bin/ccmemory")
    assert installer.uninstall(settings_path=settings) is True
    data = json.loads(settings.read_text())
    all_cmds = [h["command"] for e_list in data.get("hooks", {}).values() for e in e_list for h in e["hooks"]]
    assert "/foreign/hook.sh" in all_cmds
    assert not any("ccmemory" in c for c in all_cmds)


def test_uninstall_no_op_when_not_installed(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    assert installer.uninstall(settings_path=settings) is False
