"""The worker's MCP server (`ccdirector mcp`): tools that block at no cost."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ccdirector import mcpserver, plan, procs, work, workers

REPO = Path(__file__).resolve().parents[1]
SHIM = REPO.parent / "ccenvmcp"
PLAN = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"],
        "resources": {"rig": {"capacity": 1, "what": "the rig"},
                      "pool": {"capacity": 4, "what": "four nodes"}},
        "items": [{"id": "a", "what": "do a", "serves": ["c"], "priority": 1, "paths": [],
                   "check": "true", "size": "S"},
                  {"id": "b", "what": "do b", "serves": ["c"], "priority": 1, "paths": [],
                   "check": "true", "size": "S", "depends_on": ["a"]}],
        "risks": []}


def setup(root):
    plan.write(root, PLAN)
    work.sync(root, PLAN)


def call(mid, name, args=None, token=None):
    params = {"name": name, "arguments": args or {}}
    if token is not None:
        params["_meta"] = {"progressToken": token}
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": params}


def text(reply):
    return reply["result"]["content"][0]["text"]


class Server:
    """A real `ccdirector mcp` process for one worker, over stdio."""

    def __init__(self, root, worker):
        env = dict(os.environ, CCDIRECTOR_ROOT=str(root), CCDIRECTOR_WORKER=worker,
                   PYTHONPATH=os.pathsep.join([str(REPO / "src"), str(SHIM)]))
        self.proc = subprocess.Popen([sys.executable, "-m", "ccdirector.cli", "mcp"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, env=env, cwd=str(root))

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=20)


def test_two_workers_contend_for_the_rig_over_real_pipes(tmp_path):
    setup(tmp_path)
    one, two = Server(tmp_path, "w1"), Server(tmp_path, "w2")
    try:
        one.send(call(1, "acquire", {"resource": "rig", "why": "lap 1"}))
        lease = json.loads(text(one.read()))["lease"]
        two.send(call(1, "acquire", {"resource": "rig", "why": "lap 2"}))
        time.sleep(1.5)
        two.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert two.read() == {"jsonrpc": "2.0", "id": 2, "result": {}}, \
            "w2 is waiting, and still answers"
        one.send(call(2, "resources"))
        status = text(one.read())
        assert "rig 1/1, held by w1 1, waiting: w2 1" in status
        one.send(call(3, "release", {"lease": lease}))
        assert json.loads(text(one.read()))["released"] == lease
        got = json.loads(text(two.read()))
        assert got["resource"] == "rig" and got["waited_seconds"] >= 1
    finally:
        one.close()
        two.close()


def capture(app):
    out = []
    app._write = out.append
    return out


def replies(out):
    return {m["id"]: m for m in out if "id" in m}


def test_a_waiting_acquire_reports_progress_and_leaves_the_queue_when_cancelled(
        tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(mcpserver, "PROGRESS_SECONDS", 0.2)
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    work.acquire(tmp_path, "w1", "held", "rig", 1, "x", os.getpid(), procs.identity(os.getpid()))
    app = mcpserver.build(tmp_path, "w2")

    async def scenario():
        out = capture(app)
        app.start_call(call(7, "acquire", {"resource": "rig"}, token="p"))
        await asyncio.sleep(0.6)
        assert any(m.get("method") == "notifications/progress" for m in out)
        assert [q["worker"] for q in work.load(tmp_path)["queue"]] == ["w2"]
        assert workers.beat_path(tmp_path, "w2").exists(), "the pool can see it waiting"
        app.cancel_call({"requestId": 7})
        await asyncio.sleep(0.2)
        assert 7 not in replies(out), "a cancelled call gets no reply"

    asyncio.run(scenario())
    assert work.load(tmp_path)["queue"] == []


def test_acquire_refuses_unknown_resources_and_impossible_units(tmp_path):
    setup(tmp_path)
    app = mcpserver.build(tmp_path, "w1")
    unknown = asyncio.run(app._handle(call(1, "acquire", {"resource": "the big rig"})))
    assert unknown["result"]["isError"] and "known: pool, rig" in text(unknown)
    too_many = asyncio.run(app._handle(call(2, "acquire", {"resource": "pool", "units": 5})))
    assert too_many["result"]["isError"] and "1 to 4" in text(too_many)


def test_run_returns_exit_and_tail_and_keeps_the_whole_log(tmp_path):
    setup(tmp_path)
    app = mcpserver.build(tmp_path, "w1")
    reply = asyncio.run(app._handle(call(1, "run", {
        "command": "for i in $(seq 1 200); do echo line $i; done; exit 3"})))
    result = json.loads(text(reply))
    assert result["exit"] == 3 and not result["timed_out"]
    assert result["tail"].splitlines()[-1] == "line 200"
    assert len(result["tail"].splitlines()) == mcpserver.TAIL_LINES
    assert Path(result["log"]).read_text().count("\n") == 200


def gone(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return state == "Z"


def test_a_cancelled_run_ends_the_command_and_what_it_started(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    marker = tmp_path / "child.pid"
    app = mcpserver.build(tmp_path, "w1")

    async def scenario():
        capture(app)
        app.start_call(call(1, "run", {
            "command": f"setsid sleep 120 & echo $! > {marker}; sleep 120"}))
        for _ in range(100):
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        app.cancel_call({"requestId": 1})
        await asyncio.sleep(0.5)

    asyncio.run(scenario())
    child = int(marker.read_text())
    end = time.time() + 10
    while not gone(child):
        assert time.time() < end, "the command's child outlived the cancel"
        time.sleep(0.1)


def test_run_stops_at_its_timeout(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    app = mcpserver.build(tmp_path, "w1")
    started = time.time()
    # a timeout is whole minutes; shorten the minute for the test
    monkeypatch.setattr(mcpserver, "MINUTE", 0.5)
    reply = asyncio.run(app._handle(call(1, "run", {"command": "sleep 60",
                                                     "timeout_minutes": 1})))
    result = json.loads(text(reply))
    assert result["timed_out"] and time.time() - started < 10


def test_item_next_blocks_until_an_item_is_ready(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    assert work.next_item(tmp_path, "w1")[1]["id"] == "a"
    app = mcpserver.build(tmp_path, "w2")

    async def scenario():
        out = capture(app)
        app.start_call(call(1, "item_next"))
        await asyncio.sleep(0.3)
        assert 1 not in replies(out), "b depends on a, which w1 holds"
        with work.locked(tmp_path):
            state = work.load(tmp_path)
            state["items"]["a"].update(status=work.DONE, worker=None)
            work.save(tmp_path, state)
        await asyncio.sleep(0.3)
        return replies(out)

    got = asyncio.run(scenario())
    assert text(got[1]).startswith("ITEM") and '"id": "b"' in text(got[1])


def test_workers_get_the_server_through_their_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("CCLOOP_CLAUDE_EXTRA_ARGS", "--verbose")
    env = workers.environment(tmp_path, "worker-2")
    config = workers.mcp_config_path(tmp_path, "worker-2")
    assert env["CCLOOP_CLAUDE_EXTRA_ARGS"] == f"--verbose --mcp-config {config}"
    server = workers.mcp_config(tmp_path, "worker-2")["mcpServers"]["ccdirector"]
    assert server["args"] == ["-m", "ccdirector.cli", "mcp"]
    assert server["env"]["CCDIRECTOR_WORKER"] == "worker-2"
    assert server["env"]["CCDIRECTOR_ROOT"] == str(tmp_path)


GOALS = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"], "risks": [],
         "items": [{"id": "g1", "what": "goal one", "serves": ["c"], "priority": 1,
                    "paths": [], "check": "test -f fixed", "size": "M"},
                   {"id": "slow", "what": "a goal proved by a long test", "serves": ["c"],
                    "priority": 2, "paths": [], "check": "sleep 2; test -f fixed",
                    "size": "L"},
                   {"id": "capped", "what": "a goal with a limit", "serves": ["c"],
                    "priority": 3, "paths": [], "check": "sleep 30", "check_timeout": 1,
                    "size": "S"}]}


def goals(root, worker, item_id):
    plan.write(root, GOALS)
    work.sync(root, GOALS)
    with work.locked(root):
        state = work.load(root)
        state["items"][item_id].update(status=work.CLAIMED, worker=worker)
        work.save(root, state)


def test_item_done_fails_keeps_the_item_and_passes_once_fixed(tmp_path):
    goals(tmp_path, "w1", "g1")
    app = mcpserver.build(tmp_path, "w1")
    failed = text(asyncio.run(app._handle(call(1, "item_done", {"id": "g1"}))))
    assert failed.startswith("FAILED: g1") and "exit 1" in failed and "full output:" in failed
    assert work.load(tmp_path)["items"]["g1"]["status"] == "claimed", "still w1's"
    (tmp_path / "fixed").write_text("")
    passed = text(asyncio.run(app._handle(call(2, "item_done", {"id": "g1"}))))
    assert passed.startswith("PASSED: g1")
    assert work.load(tmp_path)["items"]["g1"]["status"] == "done"
    other = mcpserver.build(tmp_path, "w2")
    refused = text(asyncio.run(other._handle(call(3, "item_done", {"id": "slow"}))))
    assert refused.startswith("REFUSED: slow"), "w2 does not hold it"


def test_item_done_refuses_until_the_consult_after_three_failures(tmp_path):
    goals(tmp_path, "w1", "g1")
    app = mcpserver.build(tmp_path, "w1")
    for n in range(3):
        last = text(asyncio.run(app._handle(call(n, "item_done", {"id": "g1"}))))
    assert last.startswith("FAILED") and "CONSULT NOW" in last
    refused = text(asyncio.run(app._handle(call(9, "item_done", {"id": "g1"}))))
    assert refused.startswith("REFUSED") and "Ask an advisor" in refused
    work.item_consulted(tmp_path, "w1")
    (tmp_path / "fixed").write_text("")
    assert text(asyncio.run(app._handle(call(10, "item_done", {"id": "g1"})))).startswith("PASSED")


def test_item_done_waits_out_a_long_check_with_progress(tmp_path, monkeypatch):
    goals(tmp_path, "w1", "slow")
    (tmp_path / "fixed").write_text("")
    monkeypatch.setattr(mcpserver, "PROGRESS_SECONDS", 0.2)
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    app = mcpserver.build(tmp_path, "w1")

    async def scenario():
        out = capture(app)
        app.start_call(call(1, "item_done", {"id": "slow"}, token="p"))
        for _ in range(100):
            if 1 in replies(out):
                break
            await asyncio.sleep(0.1)
        return out

    out = asyncio.run(scenario())
    assert any(m.get("method") == "notifications/progress" for m in out)
    assert text(replies(out)[1]).startswith("PASSED: slow")


def test_item_done_honours_a_check_timeout(tmp_path, monkeypatch):
    goals(tmp_path, "w1", "capped")
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    app = mcpserver.build(tmp_path, "w1")
    started = time.time()
    reply = text(asyncio.run(app._handle(call(1, "item_done", {"id": "capped"}))))
    assert reply.startswith("FAILED") and "timed out" in reply and time.time() - started < 10


def test_a_cancelled_item_done_ends_the_check_and_records_nothing(tmp_path, monkeypatch):
    goals(tmp_path, "w1", "g1")
    monkeypatch.setattr(mcpserver, "POLL_SECONDS", 0.05)
    marker = tmp_path / "child.pid"
    long_check = dict(GOALS)
    long_check["items"] = [dict(GOALS["items"][0],
                                check=f"setsid sleep 120 & echo $! > {marker}; sleep 120")]
    plan.write(tmp_path, long_check)
    app = mcpserver.build(tmp_path, "w1")

    async def scenario():
        out = capture(app)
        app.start_call(call(1, "item_done", {"id": "g1"}))
        for _ in range(100):
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        app.cancel_call({"requestId": 1})
        await asyncio.sleep(0.5)
        return out

    out = asyncio.run(scenario())
    assert 1 not in replies(out)
    child = int(marker.read_text())
    end = time.time() + 10
    while not gone(child):
        assert time.time() < end, "the check's child outlived the cancel"
        time.sleep(0.1)
    kinds = [e["kind"] for e in work.events(tmp_path)]
    assert "item_check_failed" not in kinds and "item_done" not in kinds
    assert work.load(tmp_path)["items"]["g1"]["status"] == "claimed"


def test_the_server_keeps_its_alive_file_moving(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(mcpserver, "PROGRESS_SECONDS", 0.1)
    alive = workers.alive_path(tmp_path, "w1")

    async def scenario():
        task = asyncio.ensure_future(mcpserver.keep_alive(alive))
        await asyncio.sleep(0.05)
        first = alive.stat().st_mtime_ns
        await asyncio.sleep(0.25)
        second = alive.stat().st_mtime_ns
        task.cancel()
        return first, second

    first, second = asyncio.run(scenario())
    assert second > first


def test_a_real_server_writes_its_alive_file(tmp_path):
    setup(tmp_path)
    server = Server(tmp_path, "w1")
    try:
        server.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert server.read()["id"] == 1
        assert workers.alive_path(tmp_path, "w1").exists()
    finally:
        server.close()
