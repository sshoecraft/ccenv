"""The claude -p adapter, against outputs recorded from real runs.

tests/fixtures/claude/*.stdout|stderr|exit were captured from claude 2.1.317:
- schema_ok: `claude -p --output-format json --json-schema ...` answering a
  two-field schema (structured_output present).
- bad_model: the same with `--model claude-does-not-exist-1` (is_error true,
  api_error_status 404).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ccdirector import adapter

FIXTURES = Path(__file__).parent / "fixtures" / "claude"


def recorded(name: str) -> tuple[int, str, str]:
    code = int((FIXTURES / f"{name}.exit").read_text().strip().split("=")[1])
    return code, (FIXTURES / f"{name}.stdout").read_text(), (FIXTURES / f"{name}.stderr").read_text()


def test_structured_output_is_returned():
    reply = adapter.interpret(*recorded("schema_ok"))
    assert reply.data == {"found": True, "goal": "say hello"}
    assert reply.model == "claude-haiku-5-5"
    assert reply.cost_usd > 0
    assert reply.turns == 2


def test_api_error_raises_with_status():
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.interpret(*recorded("bad_model"))
    assert caught.value.status == 404
    assert "claude-does-not-exist-1" in str(caught.value)


def test_empty_output_is_an_error_not_an_answer():
    with pytest.raises(adapter.AdapterError):
        adapter.interpret(0, "", "")


def test_command_restricts_tools_and_keeps_prompt_off_argv(tmp_path):
    system = tmp_path / "system.md"
    cmd = adapter.command("claude", system, {"type": "object"}, ["Read", "Grep", "Glob"], "opus")
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--system-prompt-file") + 1] == str(system)
    assert "--bare" not in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_no_tools_means_empty_tool_list(tmp_path):
    cmd = adapter.command("claude", tmp_path / "s.md", {}, [], None)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--model" not in cmd


def test_child_environment_drops_claudecode(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert "CLAUDECODE" not in adapter.child_environment()


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_timeout_is_an_error(tmp_path):
    system = tmp_path / "system.md"
    system.write_text("Answer.")
    with pytest.raises(adapter.AdapterError) as caught:
        adapter.request(system, "hello", {"type": "object"}, tmp_path, [], timeout=0.05)
    assert "within" in str(caught.value)
