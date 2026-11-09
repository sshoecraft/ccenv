"""The hooks that tell a worker its item changed and record consults."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

from ccdirector import plan, progress, session, work, workers

PLAN = {
    "goal": "Ship it.", "source": "board", "gate": "gate", "criteria": ["c"],
    "items": [
        {"id": "a", "what": "Wait until the window opens.", "serves": ["c"], "priority": 1,
         "paths": ["a"], "check": "true", "size": "S"},
        {"id": "b", "what": "B.", "serves": ["c"], "priority": 2, "paths": ["b"],
         "check": "true", "size": "S"},
    ],
}


def accepted(root, body):
    plan.write(root, body)
    work.sync(root, body)


def claimed(root, worker="w1"):
    accepted(root, PLAN)
    assert work.next_item(root, worker)[0] == "item"


def revised():
    body = copy.deepcopy(PLAN)
    body["items"][0]["what"] = "Run now; there is no window."
    return body


def run_hook(stage, root, payload=None, worker="w1"):
    env = {**os.environ, "CCDIRECTOR_ROOT": str(root)}
    env.pop("CCDIRECTOR_WORKER", None)
    if worker:
        env["CCDIRECTOR_WORKER"] = worker
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", "hook", stage],
                          input=json.dumps(payload or {}), capture_output=True, text=True, env=env)


def test_unchanged_item_says_nothing(tmp_path):
    claimed(tmp_path)
    done = run_hook("notify", tmp_path)
    assert done.returncode == 0 and done.stdout == ""


def test_a_revision_reaches_the_worker_once_on_its_next_tool_call(tmp_path):
    claimed(tmp_path)
    accepted(tmp_path, revised())
    done = run_hook("notify", tmp_path)
    context = json.loads(done.stdout)["hookSpecificOutput"]
    assert context["hookEventName"] == "PreToolUse"
    assert "Run now; there is no window." in context["additionalContext"]
    assert "Check: true" in context["additionalContext"]
    assert run_hook("notify", tmp_path).stdout == "", "delivered once"
    kinds = [e for e in work.events(tmp_path) if e["kind"] == "item_change_delivered"]
    assert [(e["item"], e["change"]) for e in kinds] == [("a", "revised")]


def test_only_the_holder_is_told(tmp_path):
    claimed(tmp_path)
    accepted(tmp_path, revised())
    assert run_hook("notify", tmp_path, worker="w2").stdout == ""
    assert "no window" in run_hook("notify", tmp_path, worker="w1").stdout


def test_resubmitting_the_same_plan_is_not_a_revision(tmp_path):
    claimed(tmp_path)
    accepted(tmp_path, copy.deepcopy(PLAN))
    assert run_hook("notify", tmp_path).stdout == ""


def test_item_next_shows_the_new_text_so_the_hook_stays_quiet(tmp_path):
    claimed(tmp_path)
    accepted(tmp_path, revised())
    kind, item = work.next_item(tmp_path, "w1")
    assert kind == "item" and "no window" in item["what"]
    assert run_hook("notify", tmp_path).stdout == ""


def test_a_stale_flag_does_not_reach_the_next_claimant(tmp_path):
    claimed(tmp_path)
    accepted(tmp_path, revised())
    work.release_worker(tmp_path, "w1")
    assert work.next_item(tmp_path, "w2")[0] == "item"
    assert run_hook("notify", tmp_path, worker="w2").stdout == ""


def test_a_dropped_item_tells_its_worker_to_stop(tmp_path):
    claimed(tmp_path)
    body = copy.deepcopy(PLAN)
    body["items"] = body["items"][1:]
    accepted(tmp_path, body)
    context = json.loads(run_hook("notify", tmp_path).stdout)["hookSpecificOutput"]
    assert "dropped your item `a`" in context["additionalContext"]
    assert run_hook("notify", tmp_path).stdout == ""


def test_notify_never_blocks_a_tool(tmp_path):
    done = run_hook("notify", tmp_path / "missing", worker=None)
    assert done.returncode == 0 and done.stdout == ""


def test_consult_records_advisor_and_who_asked(tmp_path):
    accepted(tmp_path, PLAN)
    assert run_hook("consult", tmp_path, {"tool_name": "mcp__ask_astra__query"},
                    worker=None).returncode == 0
    assert run_hook("consult", tmp_path, {"tool_name": "mcp__ask_gpt__query"}).returncode == 0
    events = [e for e in work.events(tmp_path) if e["kind"] == "consulted"]
    assert [(e["advisor"], e["by"]) for e in events] == [("astra", "director"), ("gpt", "w1")]
    record = progress.load(tmp_path)
    assert record["consults"] == 1 and record["worker_consults"] == 1


BACKGROUND_REPLY = [{"type": "text", "text": 'MCP tool "ask_astra/query" is still running after '
                     "120s. It was moved to the background as task kdgl3difr and keeps running; "
                     "you'll receive a notification with the result when it completes."}]


def transcript_line(kind, status="completed"):
    note = f"<task-notification>\n<task-id>kdgl3difr</task-id>\n<status>{status}</status>"
    if kind == "queued":
        return json.dumps({"type": "queue-operation", "operation": "enqueue", "content": note})
    return json.dumps({"type": "user", "message": {"role": "user", "content": note}})


def background(root):
    log = root / "session.jsonl"
    log.write_text("")
    done = run_hook("consult", root, {"tool_name": "mcp__ask_astra__query",
                                      "tool_response": BACKGROUND_REPLY,
                                      "transcript_path": str(log)}, worker=None)
    assert done.returncode == 0
    return log


def test_a_backgrounded_consult_is_asked_but_not_answered(tmp_path):
    accepted(tmp_path, PLAN)
    background(tmp_path)
    assert progress.load(tmp_path).get("consults", 0) == 0
    assert progress.consult_waiting(tmp_path) == ["astra"]


def test_a_queued_answer_has_not_reached_the_director(tmp_path):
    accepted(tmp_path, PLAN)
    log = background(tmp_path)
    log.write_text(transcript_line("queued") + "\n")
    assert progress.consult_waiting(tmp_path) == ["astra"]


def test_a_delivered_answer_counts_and_clears_the_wait(tmp_path):
    accepted(tmp_path, PLAN)
    log = background(tmp_path)
    log.write_text(transcript_line("queued") + "\n" + transcript_line("user") + "\n")
    assert progress.consult_waiting(tmp_path) == []
    assert progress.load(tmp_path)["consults"] == 1
    assert [e["advisor"] for e in work.events(tmp_path) if e["kind"] == "consulted"] == ["astra"]


def test_a_failed_consult_clears_the_wait_without_counting(tmp_path):
    accepted(tmp_path, PLAN)
    log = background(tmp_path)
    log.write_text(transcript_line("user", status="failed") + "\n")
    assert progress.consult_waiting(tmp_path) == []
    assert progress.load(tmp_path).get("consults", 0) == 0


def test_a_lost_consult_stops_blocking_after_the_patience_runs_out(tmp_path, monkeypatch):
    accepted(tmp_path, PLAN)
    background(tmp_path)
    monkeypatch.setattr(progress, "CONSULT_PATIENCE_SECONDS", 0)
    assert progress.consult_waiting(tmp_path) == []
    assert progress.load(tmp_path).get("consults", 0) == 0


def test_a_new_run_is_not_owed_a_consult_from_the_last_one(tmp_path):
    accepted(tmp_path, PLAN)
    background(tmp_path)
    progress.forget_pending(tmp_path)
    assert progress.consult_waiting(tmp_path) == []


def submit(root, *flags):
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", "plan", "submit",
                           "--project", str(root), *flags],
                          input=json.dumps(revised()), capture_output=True, text=True)


def test_plan_submit_waits_for_the_directors_consult_at_any_point(tmp_path):
    accepted(tmp_path, PLAN)
    log = background(tmp_path)
    refused = submit(tmp_path)
    assert refused.returncode == 1 and "astra has not answered" in refused.stdout
    log.write_text(transcript_line("user") + "\n")
    assert submit(tmp_path).returncode == 0


def test_a_workers_consult_does_not_hold_up_the_director(tmp_path):
    accepted(tmp_path, PLAN)
    log = tmp_path / "session.jsonl"
    log.write_text("")
    run_hook("consult", tmp_path, {"tool_name": "mcp__ask_astra__query",
                                   "tool_response": BACKGROUND_REPLY,
                                   "transcript_path": str(log)}, worker="w1")
    assert submit(tmp_path).returncode == 0


def test_consult_never_blocks_a_tool(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CCDIRECTOR_ROOT"}
    done = subprocess.run([sys.executable, "-m", "ccdirector.cli", "hook", "consult"],
                          input="not json", capture_output=True, text=True, env=env)
    assert done.returncode == 0


def test_worker_settings_install_notify_on_every_tool_and_consult(tmp_path):
    hooks = json.loads(workers.settings(tmp_path))["hooks"]
    notify = hooks["PreToolUse"][1]
    assert notify["matcher"] == "*" and notify["hooks"][0]["command"].endswith("hook notify")
    consult = hooks["PostToolUse"][1]
    assert consult["matcher"] == "mcp__ask_.*"
    assert consult["hooks"][0]["command"].endswith("hook consult")


def test_director_settings_record_consults_and_keep_the_edit_ban(tmp_path):
    data = json.loads(session.deny_settings(tmp_path))
    assert data["permissions"]["deny"]
    assert data["hooks"]["PostToolUse"][0]["matcher"] == "mcp__ask_.*"
