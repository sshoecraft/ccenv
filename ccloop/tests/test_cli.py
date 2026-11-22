import re

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


def test_version_is_not_hardcoded_in_the_package():
    # The old test (import __version__, assert it appears in the output) is
    # tautological: it passes no matter how far the hardcoded string has
    # drifted from pyproject.toml, which is exactly how `ccloop --version`
    # spent two releases reporting 0.10.1 while the installed dist was 0.12.0.
    # Pin the property that actually matters — there is only ONE source of
    # truth, and it is the package metadata.
    from pathlib import Path

    import ccloop

    src = Path(ccloop.__file__).read_text(encoding="utf-8")
    assert "_dist_version" in src, "__version__ must come from package metadata"
    assert not re.search(r'^__version__\s*=\s*["\']', src, re.M), \
        "__version__ is hardcoded again — it will drift from pyproject.toml"


def test_version_matches_installed_distribution():
    from importlib.metadata import PackageNotFoundError, version

    from ccloop import __version__

    try:
        assert __version__ == version("ccloop")
    except PackageNotFoundError:
        # Source checkout with no installed dist — the honest fallback.
        assert __version__ == "0+unknown"


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


def _make_run(project, run_id, task="do a thing", resume="still working",
              criteria=None, criteria_met=None, mtime=None):
    import os
    d = project / ".ccloop" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "task.md").write_text(task + "\n", encoding="utf-8")
    (d / "resume.md").write_text(resume, encoding="utf-8")
    if criteria is not None:
        (d / "criteria.md").write_text(criteria, encoding="utf-8")
    if criteria_met is not None:
        (d / "criteria-met").write_text(criteria_met, encoding="utf-8")
    if mtime is not None:
        for p in (d, d / "resume.md"):
            os.utime(p, (mtime, mtime))
    return d


RID_A = "aaaaaaaa-1111-1111-1111-111111111111"
RID_B = "bbbbbbbb-2222-2222-2222-222222222222"
RID_C = "cccccccc-3333-3333-3333-333333333333"


def test_resume_no_id_no_runs_dir(project, monkeypatch, capsys):
    captured = _stub_resume(monkeypatch)
    assert cli.main(["-i", "--resume-run"]) == 2
    assert "no runs in" in capsys.readouterr().err
    assert captured == {}


def test_resume_no_id_only_converged_runs(project, monkeypatch, capsys):
    captured = _stub_resume(monkeypatch)
    _make_run(project, RID_A, resume="DONE all finished")
    _make_run(project, RID_B, criteria="tests pass", criteria_met="YES")
    _make_run(project, RID_C, resume="")
    assert cli.main(["-i", "--resume-run"]) == 2
    assert "no unconverged runs" in capsys.readouterr().err
    assert captured == {}


def test_resume_no_id_picks_the_one_unconverged(project, monkeypatch):
    captured = _stub_resume(monkeypatch)
    _make_run(project, RID_A, resume="DONE all finished")
    _make_run(project, RID_B, criteria="tests pass", criteria_met="NO: 3 fail")
    _make_run(project, RID_C, criteria="tests pass", criteria_met="YES")
    assert cli.main(["-i", "--cutoff=80", "--resume-run"]) == 0
    assert captured["run_id"] == RID_B
    assert captured["cutoff_tokens"] == 80_000


def test_resume_no_id_several_unconverged_lists_newest_first(project, monkeypatch, capsys):
    captured = _stub_resume(monkeypatch)
    _make_run(project, RID_A, task="older task", mtime=1_700_000_000)
    _make_run(project, RID_B, task="newer task", mtime=1_700_100_000)
    _make_run(project, RID_C, resume="DONE")
    assert cli.main(["-i", "--resume-run"]) == 2
    err = capsys.readouterr().err
    assert "2 unconverged runs" in err
    assert RID_C not in err
    assert err.index(RID_B) < err.index(RID_A)
    assert "newer task" in err and "older task" in err
    assert captured == {}


def test_resume_explicit_id_still_works_with_several(project, monkeypatch):
    captured = _stub_resume(monkeypatch)
    _make_run(project, RID_A)
    _make_run(project, RID_B)
    assert cli.main(["-i", "--resume-run", RID_A]) == 0
    assert captured["run_id"] == RID_A


def test_resume_rejects_extra_args(project, monkeypatch, capsys):
    captured = _stub_resume(monkeypatch)
    assert cli.main(["-i", "--resume-run", RID_A, "extra"]) == 2
    assert "too many arguments" in capsys.readouterr().err
    assert captured == {}


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
             model=None, effort=None):
        captured["criteria"] = criteria
        captured["task"] = task
        captured["interactive"] = interactive
        captured["cutoff_tokens"] = cutoff_tokens
        captured["model"] = model
        captured["effort"] = effort
        return 0
    monkeypatch.setattr(runner, "cmd_run", fake)
    return captured


def _stub_resume(monkeypatch):
    from ccloop import runner
    captured = {}
    def fake(run_id, ensure_hook=True, interactive=False, cutoff_tokens=None,
             model=None, effort=None):
        captured["run_id"] = run_id
        captured["interactive"] = interactive
        captured["cutoff_tokens"] = cutoff_tokens
        captured["model"] = model
        captured["effort"] = effort
        return 0
    monkeypatch.setattr(runner, "cmd_resume", fake)
    return captured


def test_interactive_flag_forces_true(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "", "do a thing"])
    assert captured["interactive"] is True
    assert captured["task"] == "do a thing"


def test_headless_alone_forces_false(monkeypatch):
    # --headless alone selects headless -p, even on a TTY.
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    assert cli.main(["--headless", "", "do a thing"]) == 0
    assert captured["interactive"] is False


def test_headless_with_legacy_accept_flag_still_runs(monkeypatch):
    # Existing invocations that pass --accept-api-cost keep working.
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(True))
    monkeypatch.setattr("sys.stdout", _FakeStream(True))
    assert cli.main(["--headless", "--accept-api-cost", "", "do a thing"]) == 0
    assert captured["interactive"] is False
    assert captured["task"] == "do a thing"


def test_no_tty_headless_runs(monkeypatch):
    captured = _stub_run(monkeypatch)
    monkeypatch.setattr("sys.stdin", _FakeStream(False))
    monkeypatch.setattr("sys.stdout", _FakeStream(False))
    assert cli.main(["--headless", "", "do a thing"]) == 0
    assert captured["interactive"] is False


def test_interactive_and_headless_mutually_exclusive(monkeypatch, capsys):
    captured = _stub_run(monkeypatch)
    assert cli.main(["-i", "--headless", "", "x"]) == 2
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
    # No TTY and no --headless → error out; the mode is never picked silently.
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


# ── --effort parsing ─────────────────────────────────────────────────────


def test_effort_default_is_none(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "", "task"])
    assert captured["effort"] is None


def test_effort_eq_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--effort=max", "", "task"])
    assert captured["effort"] == "max"


def test_effort_space_form_parses(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--effort", "high", "", "task"])
    assert captured["effort"] == "high"


def test_effort_missing_value_rejected(capsys):
    assert cli.main(["--effort"]) == 2
    assert "--effort" in capsys.readouterr().err


def test_effort_empty_value_rejected(capsys):
    assert cli.main(["--effort=", "", "task"]) == 2
    assert "--effort" in capsys.readouterr().err


def test_effort_threads_into_resume(monkeypatch):
    captured = _stub_resume(monkeypatch)
    cli.main(["-i", "--effort=max", "--resume-run", "12345678-1234-1234-1234-123456789abc"])
    assert captured["effort"] == "max"


def test_effort_combines_with_model_and_cutoff(monkeypatch):
    captured = _stub_run(monkeypatch)
    cli.main(["-i", "--model=opus", "--effort=max", "--cutoff=500", "", "task"])
    assert captured["model"] == "opus"
    assert captured["effort"] == "max"
    assert captured["cutoff_tokens"] == 500000
    assert captured["task"] == "task"
