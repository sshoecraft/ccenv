import io
import json

import pytest

from ccloop import delegate


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("CCLOOP_DELEGATE", raising=False)
    monkeypatch.delenv("CCLOOP_DELEGATE_ADVISE", raising=False)
    monkeypatch.delenv("CCLOOP_DELEGATE_DENY", raising=False)
    monkeypatch.delenv("CCLOOP_RUN_ID", raising=False)
    monkeypatch.delenv("CCLOOP_RESUME_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def run(monkeypatch, capsys, tool="Bash", session="s1", **extra):
    payload = {"session_id": session, "tool_name": tool,
               "cwd": str(tmp_cwd), "tool_input": {"command": "ls"}}
    payload.update(extra)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = delegate.main([])
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


@pytest.fixture(autouse=True)
def _cwd(tmp_path):
    global tmp_cwd
    tmp_cwd = tmp_path / "proj"
    tmp_cwd.mkdir()
    return tmp_cwd


def hook(out):
    return (out or {}).get("hookSpecificOutput", {})


def test_quiet_below_threshold(monkeypatch, capsys):
    for _ in range(2):
        rc, out = run(monkeypatch, capsys)
        assert rc == 0 and out is None


def test_advises_at_third_bash_without_blocking(monkeypatch, capsys):
    run(monkeypatch, capsys)
    run(monkeypatch, capsys)
    rc, out = run(monkeypatch, capsys)
    h = hook(out)
    assert "additionalContext" in h
    # Advice must never block — a nudge rides on a call that runs anyway.
    assert "permissionDecision" not in h
    assert "3 Bash calls" in h["additionalContext"]


def test_read_resets_the_streak(monkeypatch, capsys):
    run(monkeypatch, capsys)
    run(monkeypatch, capsys)
    run(monkeypatch, capsys, tool="Read")
    rc, out = run(monkeypatch, capsys)
    assert out is None


def test_agent_call_resets_the_streak(monkeypatch, capsys):
    for _ in range(3):
        run(monkeypatch, capsys)
    run(monkeypatch, capsys, tool="Agent")
    rc, out = run(monkeypatch, capsys)
    assert out is None


def test_subagent_calls_pass_through(monkeypatch, capsys):
    # agent_id (NOT agent_type) is the field that marks a subagent call.
    for _ in range(12):
        rc, out = run(monkeypatch, capsys, agent_id="ag_1", agent_type="grind")
        assert rc == 0 and out is None


def test_agent_type_alone_does_not_exempt(monkeypatch, capsys):
    # An --agent main-thread session carries agent_type without agent_id;
    # it must still be braked.
    run(monkeypatch, capsys, agent_type="grind")
    run(monkeypatch, capsys, agent_type="grind")
    rc, out = run(monkeypatch, capsys, agent_type="grind")
    assert "additionalContext" in hook(out)


def test_no_deny_outside_a_ccloop_run(monkeypatch, capsys):
    for _ in range(20):
        rc, out = run(monkeypatch, capsys)
        assert hook(out).get("permissionDecision") != "deny"


def test_denies_long_chain_inside_a_run(monkeypatch, capsys):
    monkeypatch.setenv("CCLOOP_RUN_ID", "run1")
    decisions = []
    for _ in range(8):
        rc, out = run(monkeypatch, capsys)
        decisions.append(hook(out).get("permissionDecision"))
    assert decisions[-1] == "deny"
    assert decisions[:-1].count("deny") == 0


def test_deny_resets_so_one_chain_earns_one_refusal(monkeypatch, capsys):
    monkeypatch.setenv("CCLOOP_RUN_ID", "run1")
    for _ in range(8):
        run(monkeypatch, capsys)
    # Immediately after the refusal the session may proceed by hand.
    rc, out = run(monkeypatch, capsys)
    assert hook(out).get("permissionDecision") != "deny"


def test_thresholds_are_configurable(monkeypatch, capsys):
    monkeypatch.setenv("CCLOOP_RUN_ID", "run1")
    monkeypatch.setenv("CCLOOP_DELEGATE_ADVISE", "2")
    monkeypatch.setenv("CCLOOP_DELEGATE_DENY", "3")
    run(monkeypatch, capsys)
    rc, out = run(monkeypatch, capsys)
    assert "additionalContext" in hook(out)
    rc, out = run(monkeypatch, capsys)
    assert hook(out).get("permissionDecision") == "deny"


def test_off_switch(monkeypatch, capsys):
    monkeypatch.setenv("CCLOOP_DELEGATE", "off")
    monkeypatch.setenv("CCLOOP_RUN_ID", "run1")
    for _ in range(20):
        rc, out = run(monkeypatch, capsys)
        assert out is None


def test_sessions_do_not_share_a_streak(monkeypatch, capsys):
    run(monkeypatch, capsys, session="a")
    run(monkeypatch, capsys, session="a")
    rc, out = run(monkeypatch, capsys, session="b")
    assert out is None


def test_roster_names_project_agents(monkeypatch, capsys, tmp_path):
    agents = tmp_cwd / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "grind.md").write_text("---\nname: grind\n---\n")
    (agents / "scout.md").write_text("---\nname: scout\n---\n")
    run(monkeypatch, capsys)
    run(monkeypatch, capsys)
    rc, out = run(monkeypatch, capsys)
    ctx = hook(out)["additionalContext"]
    assert "grind" in ctx and "scout" in ctx


def test_roster_falls_back_when_no_agents_exist(monkeypatch, capsys):
    run(monkeypatch, capsys)
    run(monkeypatch, capsys)
    rc, out = run(monkeypatch, capsys)
    assert "general-purpose" in hook(out)["additionalContext"]


def test_malformed_stdin_fails_open(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert delegate.main([]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_deny_is_logged_to_hook_events(monkeypatch, capsys, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("CCLOOP_RUN_ID", "run1")
    monkeypatch.setenv("CCLOOP_RESUME_FILE", str(run_dir / "resume.md"))
    for _ in range(8):
        run(monkeypatch, capsys)
    log = (run_dir / "hook-events.log").read_text()
    assert "delegate-deny" in log
    assert "delegate-advise" in log


def test_blocking_waits_are_neutral(monkeypatch, capsys):
    """A session that already delegated and is blocked on the result must not
    be told to delegate. Observed live: the parent fired a subagent, then paid
    blocking Bash calls polling its tasks/*.output and tripped the refusal."""
    for cmd in ("until [ -f /tmp/x/tasks/abc.output ]; do sleep 5; done",
                "while true; do sleep 10; [ -f /tmp/done ] && break; done",
                "cat /tmp/claude-1000/-src-mxfs/sess/tasks/a01b.output"):
        for _ in range(12):
            rc, out = run(monkeypatch, capsys, tool="Bash",
                          tool_input={"command": cmd})
            assert out is None, f"wait command counted as grind: {cmd}"


def test_wait_does_not_launder_a_real_chain(monkeypatch, capsys):
    """Neutral must mean neutral: a wait in the middle of a grind chain neither
    advances nor resets it."""
    run(monkeypatch, capsys)
    run(monkeypatch, capsys)
    rc, out = run(monkeypatch, capsys, tool="Bash",
                  tool_input={"command": "until [ -f /tmp/z ]; do sleep 2; done"})
    assert out is None
    rc, out = run(monkeypatch, capsys)          # third real grind call
    assert "additionalContext" in hook(out)


def test_is_wait_does_not_match_ordinary_commands():
    from ccloop import delegate
    for cmd in ("grep -rn foo src/", "ls -la", "make -j8",
                "ssh node1 'dmesg | tail -40'", "python3 scripts/audit.py"):
        assert delegate.is_wait(cmd) is False, cmd
