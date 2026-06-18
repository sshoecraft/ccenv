import os

import pytest

from ccloop import runner


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda *a, **k: None)


def test_build_command_interactive_omits_print_and_streamjson(tmp_path):
    cfg = runner._config()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("the prompt")
    cmd = runner._build_command(cfg, "sid", prompt_file=prompt_file, interactive=True)
    assert "-p" not in cmd
    assert "--output-format" not in cmd
    assert "--session-id" in cmd and "sid" in cmd
    # Prompt injected via file, not on argv; "begin" triggers the session
    assert "--append-system-prompt-file" in cmd
    assert str(prompt_file) in cmd
    assert "the prompt" not in cmd
    assert cmd[-1] == "begin"


def test_build_command_headless_has_streamjson(tmp_path):
    cfg = runner._config()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("the prompt")
    cmd = runner._build_command(cfg, "sid", prompt_file=prompt_file, interactive=False)
    assert "-p" in cmd
    assert "stream-json" in cmd
    # Prompt content must not appear on argv — it is loaded via
    # --append-system-prompt-file so it stays out of /proc/<pid>/cmdline.
    assert "the prompt" not in cmd
    assert "--append-system-prompt-file" in cmd
    assert str(prompt_file) in cmd


def test_interactive_watcher_relays_when_halt_sentinel_appears(fake_claude, tmp_path, monkeypatch):
    """The watcher's only job now: relay when the keepgoing hook writes
    the halt sentinel. Pre-create it; the watcher must SIGTERM the TUI."""
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_SLEEP", "30")
    halt = tmp_path / "halt-watch-sess"
    halt.write_text("")

    exit_code, relayed = runner.run_session_interactive(
        [str(fake_claude)], dict(os.environ), "watch-sess",
        halt_file=halt, poll=0.2,
    )
    assert relayed is True


def test_interactive_no_relay_when_no_halt_sentinel(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_SLEEP", "0.5")  # exits on its own quickly
    halt = tmp_path / "halt-watch-sess"  # never created

    exit_code, relayed = runner.run_session_interactive(
        [str(fake_claude)], dict(os.environ), "watch-sess",
        halt_file=halt, poll=0.2,
    )
    assert relayed is False


def test_write_cutoff_new_run_writes_default(tmp_path):
    runner._write_cutoff(tmp_path, None, overwrite=True)
    assert (tmp_path / "cutoff").read_text().strip() == str(runner.DEFAULT_CUTOFF_TOKENS)


def test_write_cutoff_overwrite_replaces_value(tmp_path):
    (tmp_path / "cutoff").write_text("50000\n")
    runner._write_cutoff(tmp_path, 200000, overwrite=True)
    assert (tmp_path / "cutoff").read_text().strip() == "200000"


def test_write_cutoff_no_overwrite_keeps_existing(tmp_path):
    (tmp_path / "cutoff").write_text("50000\n")
    runner._write_cutoff(tmp_path, None, overwrite=False)
    assert (tmp_path / "cutoff").read_text().strip() == "50000"


def test_write_cutoff_no_overwrite_seeds_when_missing(tmp_path):
    runner._write_cutoff(tmp_path, None, overwrite=False)
    assert (tmp_path / "cutoff").read_text().strip() == str(runner.DEFAULT_CUTOFF_TOKENS)


def test_new_run_writes_cutoff(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("FAKE_DONE_AFTER", "1")
    runner.cmd_run("", "task", ensure_hook=False, cutoff_tokens=200000)
    run = next((project / ".ccloop" / "runs").iterdir())
    assert run.joinpath("cutoff").read_text().strip() == "200000"


def test_resume_without_cutoff_preserves_existing(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("CCLOOP_MAX_ITERATIONS", "1")
    runner.cmd_run("", "task", ensure_hook=False, cutoff_tokens=50000)
    run = next((project / ".ccloop" / "runs").iterdir())
    assert run.joinpath("cutoff").read_text().strip() == "50000"
    runner.cmd_resume(run.name, ensure_hook=False)
    assert run.joinpath("cutoff").read_text().strip() == "50000"


def test_resume_with_cutoff_overwrites(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("CCLOOP_MAX_ITERATIONS", "1")
    runner.cmd_run("", "task", ensure_hook=False, cutoff_tokens=50000)
    run = next((project / ".ccloop" / "runs").iterdir())
    runner.cmd_resume(run.name, ensure_hook=False, cutoff_tokens=300000)
    assert run.joinpath("cutoff").read_text().strip() == "300000"


def test_loop_converges_on_done(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("FAKE_DONE_AFTER", "2")
    rc = runner.cmd_run("", "do the thing", ensure_hook=False)
    assert rc == 0
    runs = list((project / ".ccloop" / "runs").iterdir())
    assert len(runs) == 1
    run = runs[0]
    assert run.joinpath("sessions.log").read_text().count("\n") == 2
    assert run.joinpath("resume.md").read_text().strip() == "DONE"


def test_prompt_too_long_aborts(project, isolated_home, fake_claude, monkeypatch):
    """Wall on the very first turn with no work done = the handoff prompt is
    too big. Relaying it again would just fail, so abort."""
    monkeypatch.setenv("FAKE_MODE", "toolong")
    with pytest.raises(runner.CcloopError) as exc:
        runner.cmd_run("", "do the thing", ensure_hook=False)
    assert "Prompt is too long" in str(exc.value)


def test_prompt_too_long_after_work_relays(project, isolated_home, fake_claude, monkeypatch):
    """Wall AFTER real work = the window filled mid-session. The run must
    relay to a fresh session (the whole point of ccloop), NOT abort."""
    monkeypatch.setenv("FAKE_MODE", "wall")
    monkeypatch.setenv("CCLOOP_MAX_ITERATIONS", "2")
    rc = runner.cmd_run("", "do the thing", ensure_hook=False)  # must not raise
    assert rc == 1  # capped by max-iterations, i.e. it kept relaying
    run = next((project / ".ccloop" / "runs").iterdir())
    assert run.joinpath("sessions.log").read_text().count("\n") == 2


def test_interactive_watcher_relays_on_context_wall(fake_claude, tmp_path, monkeypatch):
    """The watcher must relay when the transcript shows the context-wall
    marker, even though no halt sentinel was ever written."""
    import json
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_SLEEP", "30")
    halt = tmp_path / "halt-watch-sess"  # never created
    wall_t = tmp_path / "transcript.jsonl"
    wall_t.write_text(json.dumps({
        "type": "assistant", "isApiErrorMessage": True,
        "message": {"content": [{"type": "text", "text": "Prompt is too long"}]},
    }) + "\n", encoding="utf-8")

    exit_code, relayed = runner.run_session_interactive(
        [str(fake_claude)], dict(os.environ), "watch-sess",
        halt_file=halt, transcript_file=wall_t, poll=0.2,
    )
    assert relayed is True


def test_stuck_aborts(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "noprogress")
    monkeypatch.setenv("CCLOOP_STUCK_LIMIT", "2")
    with pytest.raises(runner.CcloopError) as exc:
        runner.cmd_run("", "do the thing", ensure_hook=False)
    assert "no progress" in str(exc.value)


def test_max_iterations_cap(project, isolated_home, fake_claude, monkeypatch):
    # work mode that never converges; cap stops it.
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("CCLOOP_MAX_ITERATIONS", "3")
    rc = runner.cmd_run("", "never done", ensure_hook=False)
    assert rc == 1
    run = next((project / ".ccloop" / "runs").iterdir())
    assert run.joinpath("sessions.log").read_text().count("\n") == 3


def test_missing_claude_bin_aborts(project, isolated_home, monkeypatch):
    monkeypatch.setenv("CCLOOP_CLAUDE_BIN", "/nonexistent/claude-xyz")
    with pytest.raises(runner.CcloopError) as exc:
        runner.cmd_run("", "do the thing", ensure_hook=False)
    assert "claude binary not found" in str(exc.value)


def test_list_and_prune_after_run(project, isolated_home, fake_claude, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("FAKE_DONE_AFTER", "1")
    runner.cmd_run("", "do the thing", ensure_hook=False)
    capsys.readouterr()

    assert runner.cmd_list() == 0
    out = capsys.readouterr().out
    assert "done" in out

    assert runner.cmd_prune(force=False) == 0
    assert "would delete" in capsys.readouterr().out

    assert runner.cmd_prune(force=True) == 0
    assert "pruned" in capsys.readouterr().out
    assert not list((project / ".ccloop" / "runs").iterdir())


def test_resume_continues_numbering(project, isolated_home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_COUNTER", str(project / "counter"))
    monkeypatch.setenv("CCLOOP_MAX_ITERATIONS", "1")
    runner.cmd_run("", "keep going", ensure_hook=False)
    run = next((project / ".ccloop" / "runs").iterdir())
    assert run.joinpath("sessions.log").read_text().count("\n") == 1

    # Resume the same run; it should add a 2nd session (numbering continues).
    runner.cmd_resume(run.name, ensure_hook=False)
    assert run.joinpath("sessions.log").read_text().count("\n") == 2
