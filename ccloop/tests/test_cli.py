import pytest

from ccloop import cli


def test_guard_fast_path_noop(monkeypatch, capsys):
    monkeypatch.delenv("CCLOOP_RUN_ID", raising=False)
    assert cli.main(["guard"]) == 0
    assert capsys.readouterr().out == ""


def test_keepgoing_fast_path_noop(monkeypatch, capsys):
    monkeypatch.delenv("CCLOOP_RUN_ID", raising=False)
    assert cli.main(["keepgoing"]) == 0
    assert capsys.readouterr().out == ""


def test_help(capsys):
    assert cli.main(["--help"]) == 0
    assert "relay-loop wrapper" in capsys.readouterr().out


def test_no_args_shows_usage(capsys):
    assert cli.main([]) == 0
    assert "Usage:" in capsys.readouterr().out


def test_version(capsys):
    from ccloop import __version__
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_unknown_option(capsys):
    assert cli.main(["--bogus"]) == 2
    assert "unknown option" in capsys.readouterr().err


def test_one_arg_rejected(capsys):
    # Single positional is no longer valid — must be (criteria, task).
    assert cli.main(["just the task"]) == 2
    assert "two arguments required" in capsys.readouterr().err


def test_empty_task_rejected(capsys):
    assert cli.main(["", "   "]) == 2
    assert "task argument is empty" in capsys.readouterr().err


def test_too_many_args_rejected(capsys):
    assert cli.main(["criteria", "task", "extra"]) == 2
    assert "too many positional" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["../etc", "not-a-uuid", "FOO-BAR", "; rm -rf /"])
def test_resume_rejects_non_uuid(bad, capsys):
    assert cli.main(["--resume-run", bad]) == 2
    assert "invalid run-id" in capsys.readouterr().err


def test_resume_requires_arg(capsys):
    assert cli.main(["--resume-run"]) == 2


def test_list_empty(project, capsys):
    assert cli.main(["--list"]) == 0
    assert "no runs" in capsys.readouterr().out


def test_prune_empty(project, capsys):
    assert cli.main(["--prune"]) == 0
    assert "no runs" in capsys.readouterr().out


def test_prune_rejects_extra_option(project, capsys):
    assert cli.main(["--prune", "--bogus"]) == 2


class _FakeStream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def _stub_run(monkeypatch):
    from ccloop import runner
    captured = {}
    def fake(criteria, task, ensure_hook=True, interactive=False, cutoff_tokens=None,
             model=None):
        captured["criteria"] = criteria
        captured["task"] = task
        captured["interactive"] = interactive
        captured["cutoff_tokens"] = cutoff_tokens
        captured["model"] = model
        return 0
    monkeypatch.setattr(runner, "cmd_run", fake)
    return captured


def _stub_resume(monkeypatch):
    from ccloop import runner
    captured = {}
    def fake(run_id, ensure_hook=True, interactive=False, cutoff_tokens=None,
             model=None):
        captured["run_id"] = run_id
        captured["interactive"] = interactive
        captured["cutoff_tokens"] = cutoff_tokens
        captured["model"] = model
        return 0
    monkeypatch.setattr(runner, "cmd_resume", fake)
    return captured


def test_interactive_flag_forces_true(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "", "do a thing"])
    assert captured["interactive"] is True
    assert captured["task"] == "do a thing"


def test_headless_without_accept_errors(monkeypatch, capsys):
    # --headless alone must NOT run -p — it requires --accept-api-cost too.
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    assert cli.main(["--headless", "", "do a thing"]) == 2
    assert "--accept-api-cost" in capsys.readouterr().err
    assert "interactive" not in captured  # run never dispatched


def test_headless_with_accept_forces_false(monkeypatch):
    captured = _stub_run(monkeypatch)
    # Even on a TTY, the two flags together select headless -p.
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    assert cli.main(["--headless", "--accept-api-cost", "", "do a thing"]) == 0
    assert captured["interactive"] is False


def test_no_tty_headless_authorized_runs(monkeypatch):
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(False))
    monkeypatch.setattr("sys.stdout", _FakeStream(False))
    assert cli.main(["--headless", "--accept-api-cost", "", "do a thing"]) == 0
    assert captured["interactive"] is False


def test_interactive_and_headless_mutually_exclusive(monkeypatch, capsys):
    captured = _stub_run(monkeypatch)
    assert cli.main(["-i", "--headless", "--accept-api-cost", "", "x"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert "interactive" not in captured


def test_accept_api_cost_alone_is_ignored_on_tty(monkeypatch):
    # --accept-api-cost without --headless does nothing; TTY → interactive.
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    assert cli.main(["--accept-api-cost", "", "do a thing"]) == 0
    assert captured["interactive"] is True


def test_autodetect_interactive_on_tty(monkeypatch):
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    cli.main(["", "do a thing"])
    assert captured["interactive"] is True


def test_no_tty_no_auth_errors(monkeypatch, capsys):
    # No TTY and no --headless --accept-api-cost → error out, never silent -p.
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(False))
    monkeypatch.setattr("sys.stdout", _FakeStream(False))
    assert cli.main(["", "do a thing"]) == 2
    assert "no TTY" in capsys.readouterr().err
    assert "interactive" not in captured  # run never dispatched


def test_no_tty_resume_no_auth_errors(monkeypatch, capsys):
    captured = _stub_resume(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(False))
    monkeypatch.setattr("sys.stdout", _FakeStream(False))
    rid = "12345678-1234-1234-1234-123456789abc"
    assert cli.main(["--resume-run", rid]) == 2
    assert "no TTY" in capsys.readouterr().err
    assert "interactive" not in captured


def test_criteria_passed_through(monkeypatch):
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    cli.main(["all tests pass with zero errors", "fix the bug"])
    assert captured["criteria"] == "all tests pass with zero errors"
    assert captured["task"] == "fix the bug"


# ── --cutoff parsing ────────────────────────────────────────────────────


def test_cutoff_default_is_none(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "", "task"])
    assert captured["cutoff_tokens"] is None


def test_cutoff_eq_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--cutoff=200", "", "task"])
    assert captured["cutoff_tokens"] == 200000


def test_cutoff_space_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--cutoff", "50", "", "task"])
    assert captured["cutoff_tokens"] == 50000


def test_cutoff_non_integer_rejected(capsys):
    assert cli.main(["--cutoff=garbage", "", "task"]) == 2
    assert "--cutoff" in capsys.readouterr().err


def test_cutoff_negative_rejected(capsys):
    assert cli.main(["--cutoff=-1", "", "task"]) == 2
    assert "--cutoff" in capsys.readouterr().err


def test_cutoff_zero_means_no_cutoff(monkeypatch):
    # 0 is the explicit "no cutoff" sentinel — accepted, threads through as 0.
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--cutoff=0", "", "task"])
    assert captured["cutoff_tokens"] == 0


def test_cutoff_missing_value_rejected(capsys):
    assert cli.main(["--cutoff"]) == 2
    assert "--cutoff" in capsys.readouterr().err


def test_cutoff_threads_into_resume(monkeypatch):
    captured = _stub_resume(monkeypatch)
    cli.main(["-i", "--cutoff=80", "--resume-run", "12345678-1234-1234-1234-123456789abc"])
    assert captured["cutoff_tokens"] == 80000


# ── --model parsing ─────────────────────────────────────────────────────


def test_model_default_is_none(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "", "task"])
    assert captured["model"] is None


def test_model_eq_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--model=opus", "", "task"])
    assert captured["model"] == "opus"


def test_model_space_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--model", "claude-opus-4-8", "", "task"])
    assert captured["model"] == "claude-opus-4-8"


def test_model_missing_value_rejected(capsys):
    assert cli.main(["--model"]) == 2
    assert "--model" in capsys.readouterr().err


def test_model_empty_value_rejected(capsys):
    assert cli.main(["--model=", "", "task"]) == 2
    assert "--model" in capsys.readouterr().err


def test_model_threads_into_resume(monkeypatch):
    captured = _stub_resume(monkeypatch)
    cli.main(["-i", "--model=opus", "--resume-run", "12345678-1234-1234-1234-123456789abc"])
    assert captured["model"] == "opus"


def test_model_combines_with_cutoff(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--model=opus", "--cutoff=500", "", "task"])
    assert captured["model"] == "opus"
    assert captured["cutoff_tokens"] == 500000
    assert captured["task"] == "task"
