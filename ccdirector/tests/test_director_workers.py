"""What a worker session is launched with, and the worker-facing commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ccdirector import plan, work, workers

#: The source tree, so the subprocesses run this code, not an installed copy.
REPO = str(Path(__file__).resolve().parents[1] / "src")


def test_settings_deny_coordination_state_and_install_the_lock_hooks(tmp_path):
    data = json.loads(workers.settings(tmp_path))
    base = "//" + str(tmp_path).lstrip("/")
    assert data["permissions"]["deny"] == [f"Edit({base}/.ccdirector/**)", f"Edit({base}/.ccloop/**)"]
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == workers.EDIT_TOOLS
    assert pre["hooks"][0]["command"].endswith("-m ccdirector.cli hook pre")
    assert sys.executable in pre["hooks"][0]["command"]
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"].endswith("hook post")


def test_environment_names_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CCLOOP_RUN_ID", "director-run")
    env = workers.environment(tmp_path, "worker-2")
    assert env["CCDIRECTOR_ROLE"] == "worker" and env["CCDIRECTOR_WORKER"] == "worker-2"
    assert env["CCDIRECTOR_ROOT"] == str(tmp_path)
    assert "CLAUDECODE" not in env and "CCLOOP_RUN_ID" not in env
    assert env["CCMEMORY_NO_COMPACTION"] == "1", "a worker's quota is for its items"
    assert json.loads(env["CCLOOP_SETTINGS"])["hooks"]


def test_task_and_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(workers.session, "ccloop", lambda: "/usr/bin/ccloop")
    text = workers.task(tmp_path, "worker-3")
    assert "You are worker-3" in text and str(tmp_path) in text and "{name}" not in text
    args = workers.argv(tmp_path, "worker-3",
                        workers.Options(count=2, cutoff=0, effort="high", model="sonnet"))
    assert args[:5] == ["/usr/bin/ccloop", "--headless", "--model=sonnet", "--cutoff=0",
                        "--effort=high"]
    assert args[5] == workers.CRITERIA and args[6] == text


def ccdirector(root, *args, worker="worker-1", run_id=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CCDIRECTOR_WORKER", "CCLOOP_RUN_ID", "CCDIRECTOR_ROOT")}
    env["PYTHONPATH"] = REPO
    env["CCDIRECTOR_ROOT"] = str(root)
    if worker:
        env["CCDIRECTOR_WORKER"] = worker
    if run_id:
        env["CCLOOP_RUN_ID"] = run_id
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", *args],
                          capture_output=True, text=True, env=env, cwd="/")


PLAN = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"], "risks": [],
        "items": [{"id": "a", "what": "make the file", "serves": ["c"], "priority": 1,
                   "paths": ["made"], "check": "test -f made", "size": "S"}]}


def test_item_commands_end_to_end(tmp_path):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    run_dir = tmp_path / ".ccloop" / "runs" / "w1run"
    run_dir.mkdir(parents=True)
    taken = ccdirector(tmp_path, "item", "next")
    assert taken.stdout.startswith("ITEM") and '"id": "a"' in taken.stdout
    assert ccdirector(tmp_path, "item", "next", worker="worker-2").stdout.startswith("WAIT"), \
        "a is in progress, so the plan is not finished"
    failed = ccdirector(tmp_path, "item", "done", "a")
    assert failed.returncode == 1 and failed.stdout.startswith("FAILED")
    (tmp_path / "made").write_text("")
    assert ccdirector(tmp_path, "item", "done", "a").stdout.startswith("PASSED")
    last = ccdirector(tmp_path, "item", "next", run_id="w1run")
    assert last.stdout.startswith("NONE") and "wrote" in last.stdout
    assert (run_dir / "criteria-met").read_text() == "YES\n"


def test_item_park(tmp_path):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    ccdirector(tmp_path, "item", "next")
    assert ccdirector(tmp_path, "item", "park", "a").returncode != 0, "a reason is required"
    parked = ccdirector(tmp_path, "item", "park", "a", "--reason", "needs the rig")
    assert parked.stdout.startswith("PARKED")
    assert work.load(tmp_path)["items"]["a"]["status"] == "parked"


def test_item_park_until_then_resumed(tmp_path):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    ccdirector(tmp_path, "item", "next")
    waiting = ccdirector(tmp_path, "item", "park", "a", "--until", "test -e lap.done",
                         "--reason", "lap queued; read lap.done")
    assert waiting.returncode == 0 and waiting.stdout.startswith("WAITING"), waiting.stdout
    assert work.load(tmp_path)["items"]["a"]["status"] == "waiting"
    assert "1 waiting on other items" in ccdirector(tmp_path, "status", worker=None).stdout
    (tmp_path / "lap.done").write_text("")
    work.wake_waiting(tmp_path)
    back = ccdirector(tmp_path, "item", "next")
    assert back.stdout.startswith("ITEM") and "RESUMED" in back.stdout
    assert "lap queued; read lap.done" in back.stdout


TWO = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"], "risks": [],
       "items": [dict(PLAN["items"][0]),
                 {"id": "b", "what": "use the file", "serves": ["c"], "priority": 1,
                  "paths": [], "check": "true", "size": "S"}]}


def test_item_park_after_and_failed_checks_keep_the_item(tmp_path):
    plan.write(tmp_path, TWO)
    work.sync(tmp_path, TWO)
    ccdirector(tmp_path, "item", "next")
    ccdirector(tmp_path, "item", "next", worker="worker-2")
    outputs = [ccdirector(tmp_path, "item", "done", "a").stdout for n in range(3)]
    assert all(o.startswith("FAILED") for o in outputs), "no PARKED outcome any more"
    assert work.load(tmp_path)["items"]["a"]["worker"] == "worker-1"
    waiting = ccdirector(tmp_path, "item", "park", "b", "--after", "a", "--reason",
                         "needs the file", worker="worker-2")
    assert waiting.stdout.startswith("WAITING") and "a is done" in waiting.stdout
    assert work.load(tmp_path)["items"]["b"]["status"] == "waiting"


def test_item_commands_need_a_worker_name(tmp_path):
    done = ccdirector(tmp_path, "item", "next", worker=None)
    assert done.returncode != 0 and "CCDIRECTOR_WORKER" in done.stderr


def test_wait_reports_stalled_and_goal_stuck_is_gated(tmp_path):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    (tmp_path / ".ccloop" / "runs" / "d1").mkdir(parents=True)
    refused = ccdirector(tmp_path, "goal-stuck", "--probe", "test -e rig-online", "the", "rig",
                         "is", "down", worker=None, run_id="d1")
    assert refused.returncode == 1 and "still in progress or can still be handed out" in refused.stdout
    ccdirector(tmp_path, "item", "next")
    ccdirector(tmp_path, "item", "park", "a", "--reason", "rig down")
    waited = ccdirector(tmp_path, "wait", "--timeout", "1", worker=None)
    assert "item_parked" in waited.stdout and "STALLED" in waited.stdout
    assert waited.stdout.startswith("PROGRESS items 0/"), "the screen keeps only the top lines"
    unproven = ccdirector(tmp_path, "goal-stuck", "the", "rig", "is", "down", worker=None,
                          run_id="d1")
    assert unproven.returncode == 1 and "--probe" in unproven.stdout
    rig = tmp_path / "rig-online"
    stuck = ccdirector(tmp_path, "goal-stuck", "--probe", f"test -e {rig}", "the", "rig",
                       "is", "down", worker=None, run_id="d1")
    assert stuck.returncode == 0 and stuck.stdout.startswith("STOPPED")
    record = json.loads((plan.folder(tmp_path) / "stuck.json").read_text())
    assert record["reason"] == "the rig is down" and record["probe"] == f"test -e {rig}"
    assert record["probe_output"].startswith("exit 1")
    assert (tmp_path / ".ccloop" / "runs" / "d1" / "criteria-met").exists()


def stalled(root):
    plan.write(root, PLAN)
    work.sync(root, PLAN)
    (root / ".ccloop" / "runs" / "d1").mkdir(parents=True)
    ccdirector(root, "item", "next")
    ccdirector(root, "item", "park", "a", "--reason", "rig down")


def test_goal_stuck_is_refused_while_the_probe_succeeds(tmp_path):
    stalled(tmp_path)
    (tmp_path / "rig-online").write_text("")
    refused = ccdirector(tmp_path, "goal-stuck", "--probe", "test -e rig-online", "an",
                         "advisor", "said", "stop", worker=None, run_id="d1")
    assert refused.returncode == 1 and "so that dependency is working" in refused.stdout
    assert "an advisor saying stop" in refused.stdout
    assert not (plan.folder(tmp_path) / "stuck.json").exists()
    assert not (tmp_path / ".ccloop" / "runs" / "d1" / "criteria-met").exists()
    assert [e["kind"] for e in work.events(tmp_path)][-1] == "goal_stuck_refused"


@pytest.mark.parametrize("probe", ["false", "/bin/false", "exit 1", "! true", "false;"])
def test_a_probe_that_fails_by_construction_is_refused(tmp_path, probe):
    stalled(tmp_path)
    refused = ccdirector(tmp_path, "goal-stuck", "--probe", probe, "nothing", "left",
                         worker=None, run_id="d1")
    assert refused.returncode == 1 and "must test the dependency itself" in refused.stdout
    assert not (plan.folder(tmp_path) / "stuck.json").exists()


def test_a_probe_that_does_not_answer_counts_as_down(tmp_path, monkeypatch):
    from ccdirector import cli
    monkeypatch.setattr(cli, "PROBE_SECONDS", 0.2)
    works, output = cli.run_probe(tmp_path, "sleep 5")
    assert not works and "no answer" in output
    assert cli.run_probe(tmp_path, "true") == (True, "exit 0")


@pytest.mark.skipif(not __import__("shutil").which("ccproject_criteria.py"),
                    reason="ccproject tools not on PATH")
def test_goal_done_runs_the_gate(tmp_path):
    def criteria(*args):
        subprocess.run(["ccproject_criteria.py", *args], cwd=str(tmp_path), check=True,
                       capture_output=True)
    criteria("add", "-i", "c", "-r", "it works", "-d", "test -f made")
    (tmp_path / ".ccloop" / "runs" / "d1").mkdir(parents=True)
    refused = ccdirector(tmp_path, "goal-done", worker=None, run_id="d1")
    assert refused.returncode == 1 and "does not pass" in refused.stdout
    criteria("update", "c", "-s", "PASS", "-m", "file exists")
    done = ccdirector(tmp_path, "goal-done", worker=None, run_id="d1")
    assert done.returncode == 0 and done.stdout.startswith("DONE")


TWO = {**PLAN, "items": [PLAN["items"][0],
                         {**PLAN["items"][0], "id": "b", "paths": ["other"]}]}


def fake(monkeypatch, code):
    """Workers that run `code` instead of ccloop."""
    monkeypatch.setattr(workers, "argv",
                        lambda root, name, options: [sys.executable, "-c", code])


def settle(pool):
    for slot in pool.slots.values():
        if slot.proc is not None:
            slot.proc.wait(timeout=20)


def kinds(root):
    return [e["kind"] for e in work.events(root)]


def running(pool):
    return sorted(n for n, s in pool.slots.items() if s.proc is not None)


def test_quick_deaths_back_off_and_the_pool_never_stops_trying(tmp_path, monkeypatch):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    fake(monkeypatch, "raise SystemExit(1)")
    pool = workers.Pool(tmp_path, workers.Options(count=1))
    slot = pool.slots["worker-1"]
    now, waits = time.time(), []
    for launch in range(10):
        pool.tick(now)
        assert slot.proc is not None, f"launch {launch + 1} did not happen"
        settle(pool)
        now += 1
        pool.tick(now)
        assert slot.proc is None and slot.state == workers.BACKOFF
        waits.append(round(slot.retry_at - now))
        now = max(slot.retry_at, pool.retry_at)
    assert waits == [15, 30, 60, 120, 240, 480, 900, 900, 900, 900]
    seen = kinds(tmp_path)
    assert seen.count("worker_started") == 10 and seen.count("workers_failing") == 1
    assert pool.outage and work.load(tmp_path)["pool"]["outage"] is True
    assert work.load(tmp_path)["workers"]["worker-1"]["restarts"] == 9


def test_an_outage_tries_one_worker_at_a_time_and_ends_when_one_stays_up(tmp_path, monkeypatch):
    plan.write(tmp_path, TWO)
    work.sync(tmp_path, TWO)
    fake(monkeypatch, "raise SystemExit(1)")
    pool = workers.Pool(tmp_path, workers.Options(count=2))
    now = time.time()
    for _ in range(2):
        pool.tick(now)
        assert running(pool) == ["worker-1", "worker-2"]
        settle(pool)
        now += 1
        pool.tick(now)
        now += workers.BACKOFF_SECONDS
    assert pool.outage and "workers_failing" in kinds(tmp_path)
    fake(monkeypatch, "import time; time.sleep(60)")
    now = max(now, pool.retry_at)
    pool.tick(now)
    assert running(pool) == ["worker-1"], "only one worker is tried during an outage"
    pool.tick(now + 5)
    assert running(pool) == ["worker-1"], "no second try while the first is starting"
    pool.tick(now + workers.QUICK_EXIT_SECONDS + 1)
    assert not pool.outage and "workers_resumed" in kinds(tmp_path)
    assert running(pool) == ["worker-1", "worker-2"], "every slot comes back at once"
    assert work.load(tmp_path)["pool"]["outage"] is False
    pool.stop()
    assert running(pool) == []
    assert kinds(tmp_path).count("workers_failing") == 1, "stopping is not a failure"


def test_a_silent_worker_is_ended_and_relaunched_after_a_wait(tmp_path, monkeypatch):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    fake(monkeypatch, "import time; time.sleep(120)")
    pool = workers.Pool(tmp_path, workers.Options(count=1, stall=1))
    slot = pool.slots["worker-1"]
    now = time.time()
    pool.tick(now)
    log = workers.log_path(tmp_path, "worker-1")
    log.write_text(log.read_text() + "Reading the item.\n")
    os.utime(log, (now, now))
    pool.tick(now + 59)
    assert not slot.stall
    pool.tick(now + 61)
    assert slot.stall == "no output for 1 minutes"
    slot.proc.wait(timeout=20)
    pool.tick(now + 62)
    assert slot.proc is None and slot.state == workers.BACKOFF
    assert round(slot.retry_at - (now + 62)) == workers.BACKOFF_SECONDS
    exited = [e for e in work.events(tmp_path) if e["kind"] == "worker_exited"][-1]
    assert exited["stalled"] == "no output for 1 minutes" and "retry_at" in exited
    assert "worker_stalled" in kinds(tmp_path)
    assert not pool.outage and pool.failures == 0, "a hang is not an outage"
    pool.tick(slot.retry_at)
    assert slot.proc is not None and slot.restarts == 1
    pool.stop()


def test_an_open_tool_call_gets_four_times_as_long(tmp_path, monkeypatch):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    fake(monkeypatch, "import time; time.sleep(120)")
    pool = workers.Pool(tmp_path, workers.Options(count=1, stall=1))
    slot = pool.slots["worker-1"]
    now = time.time()
    pool.tick(now)
    log = workers.log_path(tmp_path, "worker-1")
    log.write_text(log.read_text() + "→ Bash     run the rig\n")
    os.utime(log, (now, now))
    pool.tick(now + 61)
    pool.tick(now + 60 * workers.TOOL_STALL_FACTOR - 1)
    assert not slot.stall
    pool.tick(now + 60 * workers.TOOL_STALL_FACTOR + 1)
    assert slot.stall == "no output for 4 minutes with a tool call open"
    pool.stop()


def test_tool_open_reads_ccloop_call_and_result_lines():
    assert workers.tool_open(["→ Bash     x"])
    assert not workers.tool_open(["→ Bash     x", "  ok 3 lines"])
    assert workers.tool_open(["→ Read     a", "→ Bash     b", "  ok 1 lines"])
    assert not workers.tool_open(["→ Bash     x", "  ERROR: boom"])
    assert not workers.tool_open(["→ Bash     x", "[ccloop] ── session 2 ── id=abc"])
    assert not workers.tool_open(["Thinking about it.", ""])


def gone(pid):
    try:
        state = (Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0])
    except OSError:
        return True
    return state == "Z"


def test_stop_ends_processes_that_left_the_worker_process_group(tmp_path, monkeypatch):
    """ccloop starts claude with a session of its own; ending the group would miss it."""
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    marker = tmp_path / "grandchild.pid"
    fake(monkeypatch,
         "import subprocess, sys, time\n"
         "p = subprocess.Popen(['sleep', '120'], start_new_session=True)\n"
         f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
         "time.sleep(120)\n")
    pool = workers.Pool(tmp_path, workers.Options(count=1))
    pool.tick(time.time())
    end = time.time() + 20
    while not marker.exists() or not marker.read_text():
        assert time.time() < end, "the fake worker never started its child"
        time.sleep(0.1)
    grandchild = int(marker.read_text())
    assert not gone(grandchild)
    pool.stop()
    end = time.time() + 10
    while not gone(grandchild):
        assert time.time() < end, "the grandchild outlived the pool"
        time.sleep(0.1)


def test_progress_shows_the_pool_and_an_outage(tmp_path):
    from ccdirector import progress
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    work.set_pool(tmp_path, outage=True, failures=3, retry_at="2026-11-13T14:32:00+00:00")
    work.set_worker(tmp_path, "worker-1", state="backoff",
                    retry_at="2026-11-13T14:32:00+00:00", restarts=2)
    work.set_worker(tmp_path, "worker-2", state="starting", restarts=0)
    lines = progress.pool_lines(work.load(tmp_path))
    assert lines[0] == "  pool: starting worker-2; backing off worker-1 until 14:32Z; relaunched 2x"
    assert lines[1].startswith("WORKERS PAUSED: 3 workers in a row") and "14:32Z" in lines[1]
    assert "workers backing off: worker-1 until 2026-11-13T14:32:00+00:00" in work.summary(tmp_path)


def test_a_worker_waiting_in_its_own_tools_is_not_hung(tmp_path, monkeypatch):
    """Blocked in acquire or run, a worker's log is silent, but its MCP server
    touches the beat file every minute."""
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    fake(monkeypatch, "import time; time.sleep(120)")
    pool = workers.Pool(tmp_path, workers.Options(count=1, stall=1))
    slot = pool.slots["worker-1"]
    now = time.time()
    pool.tick(now)
    log = workers.log_path(tmp_path, "worker-1")
    log.write_text(log.read_text() + "→ mcp__ccdirector__acquire  rig\n")
    os.utime(log, (now, now))
    beat = workers.beat_path(tmp_path, "worker-1")
    later = now + 60 * workers.TOOL_STALL_FACTOR + 60
    beat.touch()
    os.utime(beat, (later - 30, later - 30))
    pool.tick(later)
    assert not slot.stall, "the beat is 30 s old: it is waiting, not hung"
    pool.tick(later + 60 * workers.TOOL_STALL_FACTOR + 31)
    assert slot.stall, "once the beat stops too, it is hung"
    pool.stop()


def test_the_pool_returns_a_waiting_item_when_its_result_lands(tmp_path, monkeypatch):
    """The pool's waits thread, not the director, puts a waiting item back."""
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    work.next_item(tmp_path, "worker-1")
    assert work.park(tmp_path, "worker-1", "a", "lap queued", until="test -e lap.done")[0]
    monkeypatch.setattr(work, "UNTIL_CHECK_SECONDS", 0.2)
    pool = workers.Pool(tmp_path, workers.Options(count=1))
    pool.waker.start()
    try:
        (tmp_path / "lap.done").write_text("")
        end = time.time() + 10
        while work.load(tmp_path)["items"]["a"]["status"] != "pending" and time.time() < end:
            time.sleep(0.1)
    finally:
        pool.stopping.set()
        pool.waker.join(timeout=5)
    assert work.load(tmp_path)["items"]["a"]["status"] == "pending"
    assert not pool.waker.is_alive()


def test_the_pool_ends_a_worker_whose_mcp_server_froze(tmp_path):
    """The server touches its alive file every minute from its only thread; once it has
    come up, a file that stops moving means its tools no longer answer."""
    pool = workers.Pool(tmp_path, workers.Options(count=1, stall=0))
    slot = pool.slots["worker-1"]
    slot.started = 1000.0
    alive = workers.alive_path(tmp_path, "worker-1")
    assert pool.stalled(slot, 5000.0) == "", "no server yet: nothing to judge"
    alive.parent.mkdir(parents=True, exist_ok=True)
    alive.touch()
    os.utime(alive, (900.0, 900.0))
    assert pool.stalled(slot, 5000.0) == "", "a beat from an earlier launch does not count"
    os.utime(alive, (2000.0, 2000.0))
    assert pool.stalled(slot, 2000.0 + workers.SERVER_SILENT_SECONDS - 1) == ""
    reason = pool.stalled(slot, 2000.0 + workers.SERVER_SILENT_SECONDS + 60)
    assert "MCP server has not run for 11 minutes" in reason, "even with --worker-stall 0"
