import os
from pathlib import Path

import pytest

from ccloop import state


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("CCLOOP_STATE_HOOK", "CCLOOP_STATE_HOOK_TIMEOUT",
                 "CCLOOP_STATE_HOOK_MAX_BYTES"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def run_dir(tmp_path):
    """A real ccloop run dir: <project>/.ccloop/runs/<run-id>."""
    d = tmp_path / "proj" / ".ccloop" / "runs" / "rid-1"
    d.mkdir(parents=True)
    return d


def write_hook(run_dir, body, executable=True):
    hook = state.hook_path(run_dir)
    hook.write_text(body, encoding="utf-8")
    hook.chmod(0o755 if executable else 0o644)
    return hook


def test_paths_resolve_from_run_dir(run_dir, tmp_path):
    assert state.project_root(run_dir) == tmp_path / "proj"
    assert state.hook_path(run_dir) == tmp_path / "proj" / ".ccloop" / "state.sh"


def test_no_hook_yields_empty_block(run_dir):
    assert state.state_block(run_dir) == ""


def test_hook_stdout_lands_in_block(run_dir):
    write_hook(run_dir, "#!/bin/sh\necho 'OPEN DEFECTS: 4'\necho '1. [critical] D-0001'\n")
    out = state.state_block(run_dir, "RID", 7)
    assert state.SECTION in out
    assert "OPEN DEFECTS: 4" in out
    assert "1. [critical] D-0001" in out
    assert "supersedes" in out


def test_hook_sees_run_env_and_project_cwd(run_dir, tmp_path):
    write_hook(run_dir, "#!/bin/sh\necho \"run=$CCLOOP_RUN_ID sess=$CCLOOP_SESSION_NUM\"\n"
                        "echo \"root=$CCLOOP_PROJECT_ROOT\"\n"
                        "echo \"dir=$CCLOOP_RUN_DIR\"\npwd\n")
    out = state.state_block(run_dir, "RID-abc", 12)
    assert "run=RID-abc sess=12" in out
    assert f"root={tmp_path / 'proj'}" in out
    assert f"dir={run_dir}" in out
    # cwd is the project root, so relative paths in a hook resolve as the
    # session's own paths do.
    assert str(Path(tmp_path / "proj").resolve()) in out


def test_non_executable_hook_is_reported_not_silent(run_dir):
    write_hook(run_dir, "#!/bin/sh\necho hi\n", executable=False)
    logged = []
    out = state.state_block(run_dir, log=logged.append)
    assert "not executable" in out
    assert state.SECTION in out
    assert logged and "not executable" in logged[0]


def test_nonzero_exit_keeps_stdout_and_flags_it(run_dir):
    write_hook(run_dir, "#!/bin/sh\necho 'partial ledger'\necho 'boom' >&2\nexit 3\n")
    logged = []
    out = state.state_block(run_dir, log=logged.append)
    assert "partial ledger" in out
    assert "exited 3" in out
    assert "boom" in out
    assert logged and "exited 3" in logged[0]


def test_timeout_is_reported(run_dir, monkeypatch):
    monkeypatch.setenv("CCLOOP_STATE_HOOK_TIMEOUT", "1")
    write_hook(run_dir, "#!/bin/sh\nsleep 30\n")
    logged = []
    out = state.state_block(run_dir, log=logged.append)
    assert "timed out after 1s" in out
    assert logged and "timed out" in logged[0]


def test_empty_output_is_reported(run_dir):
    write_hook(run_dir, "#!/bin/sh\nexit 0\n")
    logged = []
    out = state.state_block(run_dir, log=logged.append)
    assert "produced no output" in out
    assert logged


def test_oversized_output_truncates_visibly(run_dir, monkeypatch):
    monkeypatch.setenv("CCLOOP_STATE_HOOK_MAX_BYTES", "200")
    write_hook(run_dir, "#!/bin/sh\nfor i in $(seq 1 200); do echo \"defect line $i\"; done\n")
    out = state.state_block(run_dir)
    assert "defect line 1" in out
    assert "truncated at 200 bytes" in out
    assert "CCLOOP_STATE_HOOK_MAX_BYTES" in out
    assert "defect line 200" not in out


def test_env_override_selects_hook(run_dir, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.sh"
    other.write_text("#!/bin/sh\necho 'from override'\n", encoding="utf-8")
    other.chmod(0o755)
    monkeypatch.setenv("CCLOOP_STATE_HOOK", str(other))
    assert state.hook_path(run_dir) == other
    assert "from override" in state.state_block(run_dir)


def test_unparseable_env_ints_fall_back_to_defaults(run_dir, monkeypatch):
    monkeypatch.setenv("CCLOOP_STATE_HOOK_TIMEOUT", "not-a-number")
    monkeypatch.setenv("CCLOOP_STATE_HOOK_MAX_BYTES", "")
    write_hook(run_dir, "#!/bin/sh\necho ok\n")
    assert "ok" in state.state_block(run_dir)


def test_hook_that_cannot_exec_does_not_raise(run_dir):
    # Executable bit set but no valid interpreter — the OS refuses the exec.
    write_hook(run_dir, "\x7fELF-not-really\n")
    logged = []
    out = state.state_block(run_dir, log=logged.append)
    assert state.SECTION in out
    assert logged
