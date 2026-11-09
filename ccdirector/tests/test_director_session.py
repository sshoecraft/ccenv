"""What ccdirector hands ccloop: deny rules, environment, and finding the run."""
from __future__ import annotations

import json

import pytest

from ccdirector import session


def test_deny_rules_use_the_absolute_form(tmp_path):
    rules = json.loads(session.deny_settings(tmp_path))["permissions"]["deny"]
    absolute = "//" + str(tmp_path).lstrip("/") + "/**"
    assert rules == [f"Edit({absolute})"]


def test_environment_adds_prompt_and_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CCLOOP_CLAUDE_EXTRA_ARGS", "--effort high")
    monkeypatch.delenv("CCLOOP_SETTINGS", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    env = session.environment(tmp_path)
    assert env["CCLOOP_CLAUDE_EXTRA_ARGS"] == f"--effort high --system-prompt-file {session.PROMPT_FILE}"
    assert json.loads(env["CCLOOP_SETTINGS"])["permissions"]["deny"]
    assert env["CCDIRECTOR_ROLE"] == "director"
    assert "CLAUDECODE" not in env


def test_existing_ccloop_settings_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("CCLOOP_SETTINGS", "/somewhere/settings.json")
    with pytest.raises(session.SessionError, match="CCLOOP_SETTINGS"):
        session.environment(tmp_path)


def test_prompt_files_ship_with_the_package():
    assert session.PROMPT_FILE.is_file()


def test_find_run_by_marker(tmp_path):
    runs = session.runs_folder(tmp_path)
    for name, body in (("aaa", "other task"), ("bbb", "task\n\nccdirector-run: 42\n")):
        (runs / name).mkdir(parents=True)
        (runs / name / "task.md").write_text(body)
    assert session.find_run(tmp_path, "ccdirector-run: 42") == runs / "bbb"
    assert session.find_run(tmp_path, "ccdirector-run: 43") is None


def test_mode_flags():
    assert session.mode_flags(False, None) == ["--interactive"]
    assert session.mode_flags(True, "opus") == ["--headless", "--model=opus"]


def test_mode_flags_pass_the_cutoff_including_zero():
    assert session.mode_flags(False, None, 0) == ["--interactive", "--cutoff=0"]
    assert session.mode_flags(True, None, 400) == ["--headless", "--cutoff=400"]


@pytest.mark.parametrize("argv, expected", [
    (["--cutoff", "0", "start"], 0),
    (["start", "--cutoff", "0"], 0),
    (["--cutoff", "100", "start", "--cutoff", "300"], 300),
    (["start"], None),
])
def test_cli_accepts_cutoff_before_or_after_start(argv, expected):
    from ccdirector import cli
    args = cli.build_parser().parse_args(argv)
    assert args.cutoff == expected
