"""The PROGRESS block: what the user sees of a run from the director's screen."""
from __future__ import annotations

import json
import os
import time

from ccdirector import ccproject, cli, plan, progress, session, work

PLAN = {
    "goal": "Ship it.", "source": "board", "gate": "gate", "criteria": ["c"],
    "items": [
        {"id": "a", "what": "A.", "serves": ["c"], "priority": 1, "paths": ["a"],
         "check": "true", "size": "S"},
        {"id": "b", "what": "B.", "serves": ["c"], "priority": 2, "paths": ["b"],
         "check": "true", "size": "S"},
    ],
}


def on_board(monkeypatch, green, total=9):
    monkeypatch.setattr(progress, "board", lambda root: (green, total))


def setup_run(root):
    plan.write(root, PLAN)
    work.sync(root, PLAN)


def test_block_sets_items_against_criteria(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    first = progress.report(tmp_path).splitlines()[0]
    assert first.startswith("PROGRESS items 0/2 done, 0 running, 0 parked")
    assert "criteria 3/9 green, no gain for <1m" in first
    assert "gate refused 0x" in first and "plan extended 0x" in first


def test_block_survives_a_board_it_cannot_read(tmp_path, monkeypatch):
    setup_run(tmp_path)
    def unreadable(*args):
        raise ccproject.ToolError("no board")

    monkeypatch.setattr(ccproject, "read_json", unreadable)
    assert "criteria unknown" in progress.report(tmp_path)


def test_board_reads_the_tools_own_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(ccproject, "read_json",
                        lambda *a: {"criteria": [], "verdict": {"green": 3, "total": 58}})
    assert progress.board(tmp_path) == (3, 58)


def test_worker_line_gives_its_last_sentence_not_tool_noise(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 1)
    work.next_item(tmp_path, "worker-1")
    logs = plan.folder(tmp_path) / "workers"
    logs.mkdir()
    (logs / "worker-1.log").write_text(
        "===== 2026 worker-1 starting =====\n"
        "Lap 4 is in prep. Meanwhile writing a grader.\n"
        "→ Bash     Peek lap 4 console progress\n"
        "  ok 3 lines\n"
        "-rw-rw-r-- 1 steve steve 1513 lapq.log\n"
        "Sun Nov  8 14:26:48 UTC 2026\n")
    line = progress.report(tmp_path).splitlines()[1]
    assert line.startswith("  worker-1 on a: active <1m ago")
    assert line.endswith("Lap 4 is in prep. Meanwhile writing a grader.")


def test_a_worker_with_no_log_is_said_so(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 1)
    work.next_item(tmp_path, "worker-1")
    assert "worker-1 on a: no log yet" in progress.report(tmp_path)


def test_refusals_are_recorded_and_warn_after_two_with_no_gain(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    progress.report(tmp_path)
    progress.record_refusal(tmp_path)
    assert "LIKELY LOOPING" not in progress.report(tmp_path)
    progress.record_refusal(tmp_path)
    text = progress.report(tmp_path)
    assert "gate refused 2x" in text and "LIKELY LOOPING: the gate has refused 2 times" in text
    kinds = [e["kind"] for e in work.events(tmp_path)]
    assert kinds.count("gate_refused") == 2


def test_a_gained_criterion_clears_the_warning(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    progress.record_refusal(tmp_path)
    progress.record_refusal(tmp_path)
    on_board(monkeypatch, 4)
    text = progress.report(tmp_path)
    assert "LIKELY LOOPING" not in text and "gate refused 2x" in text


def test_flapping_is_not_a_gain(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 5)
    progress.report(tmp_path)
    on_board(monkeypatch, 3)
    assert "criteria 3/9 green (best 5)" in progress.report(tmp_path)
    progress.record_refusal(tmp_path)
    on_board(monkeypatch, 5)
    progress.record_refusal(tmp_path)
    assert "LIKELY LOOPING" in progress.report(tmp_path)


def test_a_worker_retrying_its_item_never_counts_as_looping(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    with work.locked(tmp_path):
        for attempt in range(20):
            work.event(tmp_path, "item_check_failed", item="a", worker="worker-1")
    assert "LIKELY LOOPING" not in progress.report(tmp_path)


def test_extensions_ignore_the_first_plan_and_revisions(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 1)
    bigger = json.loads(json.dumps(PLAN))
    bigger["items"][1]["what"] = "B, revised."
    work.sync(tmp_path, bigger)
    assert progress.extensions(tmp_path) == 0
    bigger["items"].append({**bigger["items"][0], "id": "c"})
    work.sync(tmp_path, bigger)
    assert progress.extensions(tmp_path) == 1


def test_goal_done_records_a_refusal(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    monkeypatch.setattr(cli.ccproject, "check",
                        lambda *a: ccproject.Verdict(done=False, gate="gate", output="red"))
    args = cli.build_parser().parse_args(["goal-done", "--project", str(tmp_path)])
    assert cli.cmd_goal_done(args) == 1
    assert progress.load(tmp_path)["refusals"] == 1


def test_consults_are_counted_and_two_without_a_gain_read_stuck(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    progress.report(tmp_path)
    progress.record_consult(tmp_path, "astra", "director")
    first = progress.report(tmp_path)
    assert "consulted 1x, 1 since gain" in first and "STUCK" not in first
    progress.record_consult(tmp_path, "fable", "director")
    text = progress.report(tmp_path)
    assert "consulted 2x, 2 since gain" in text and "STUCK: 2 consults" in text
    events = [e for e in work.events(tmp_path) if e["kind"] == "consulted"]
    assert [(e["advisor"], e["by"]) for e in events] == [("astra", "director"), ("fable", "director")]


def test_a_workers_consult_leaves_the_directors_counts_alone(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    for new_id in ("c", "d", "e"):
        round_ended(tmp_path, new_id)
    assert progress.consult_due(tmp_path)
    progress.record_consult(tmp_path, "astra", "worker-1")
    assert progress.consult_due(tmp_path), "a worker's consult is not the director's"
    record = progress.load(tmp_path)
    assert record["replans_since_consult"] == 3 and record["worker_consults"] == 1
    text = progress.report(tmp_path)
    assert "consulted 0x, 0 since gain, workers 1x" in text and "STUCK" not in text


def test_a_gained_criterion_clears_stuck_but_keeps_the_total(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    progress.report(tmp_path)
    progress.record_consult(tmp_path, "astra", "director")
    progress.record_consult(tmp_path, "astra", "director")
    on_board(monkeypatch, 4)
    text = progress.report(tmp_path)
    assert "STUCK" not in text and "consulted 2x, 0 since gain" in text


def finish_all(root, worker="w1"):
    while True:
        kind, value = work.next_item(root, worker)
        if kind != "item":
            return
        assert work.finish(root, worker, value["id"])[0] == "done"


def extend(root, new_id):
    body = plan.load(root)
    body["items"] = body["items"] + [{**PLAN["items"][0], "id": new_id}]
    plan.write(root, body)
    work.sync(root, body)


def goal_done(root):
    return cli.cmd_goal_done(cli.build_parser().parse_args(["goal-done", "--project", str(root)]))


def red_gate(monkeypatch):
    monkeypatch.setattr(cli.ccproject, "check",
                        lambda *a: ccproject.Verdict(done=False, gate="gate", output="still red"))


def round_ended(root, new_id):
    """One more round: finish what is there, the gate refuses, the plan gains an item."""
    finish_all(root)
    code = goal_done(root)
    extend(root, new_id)
    return code


def limited(root, limit):
    (plan.folder(root) / "goal.json").write_text(json.dumps({"goal": "Ship it.",
                                                             "round_limit": limit}))


def test_a_round_needs_every_item_done(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    assert goal_done(tmp_path) == 1
    assert progress.load(tmp_path).get("replans_since_consult", 0) == 0


def test_retrying_goal_done_is_not_another_round(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    finish_all(tmp_path)
    goal_done(tmp_path)
    goal_done(tmp_path)
    record = progress.load(tmp_path)
    assert record["rounds_since_gain"] == 1 and record["replans_since_consult"] == 1


def test_consult_is_required_on_the_third_replan_only(tmp_path, monkeypatch, capsys):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    round_ended(tmp_path, "c")
    assert "REPLAN DUE" in capsys.readouterr().out and not progress.consult_due(tmp_path)
    round_ended(tmp_path, "d")
    assert "REPLAN DUE" in capsys.readouterr().out and not progress.consult_due(tmp_path)
    finish_all(tmp_path)
    goal_done(tmp_path)
    assert "CONSULT DUE" in capsys.readouterr().out and progress.consult_due(tmp_path)
    assert "CONSULT DUE: 3 replans" in progress.report(tmp_path)


def test_a_consult_resets_the_replan_count(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    for new_id in ("c", "d", "e"):
        round_ended(tmp_path, new_id)
    assert progress.consult_due(tmp_path)
    progress.record_consult(tmp_path, "astra", "director")
    assert not progress.consult_due(tmp_path)
    assert progress.load(tmp_path)["replans_since_consult"] == 0
    assert "replans since consult 0/3" in progress.report(tmp_path)


def test_no_advisor_waives_the_consult_and_resets_the_count(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    for new_id in ("c", "d"):
        round_ended(tmp_path, new_id)
    finish_all(tmp_path)
    goal_done(tmp_path)
    assert progress.consult_due(tmp_path)
    progress.replan_submitted(tmp_path)
    assert not progress.consult_due(tmp_path)
    assert progress.load(tmp_path)["replans_since_consult"] == 0


def test_no_round_limit_by_default_so_the_run_keeps_going(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    for number in range(8):
        assert round_ended(tmp_path, f"x{number}") == 1
    assert not (plan.folder(tmp_path) / "stuck.json").exists()
    assert "rounds without gain 8 |" in progress.report(tmp_path).splitlines()[0]


def test_an_explicit_limit_ends_the_run_and_says_why(tmp_path, monkeypatch, capsys):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    limited(tmp_path, 2)
    (tmp_path / ".ccloop" / "runs" / "r1").mkdir(parents=True)
    monkeypatch.setenv("CCLOOP_RUN_ID", "r1")
    assert round_ended(tmp_path, "c") == 1
    finish_all(tmp_path)
    assert goal_done(tmp_path) == 0
    assert "round limit: 2 rounds" in capsys.readouterr().out
    assert "round limit" in json.loads((plan.folder(tmp_path) / "stuck.json").read_text())["reason"]
    assert (tmp_path / ".ccloop" / "runs" / "r1" / "criteria-met").exists()


def test_a_new_criterion_resets_the_rounds(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    progress.report(tmp_path)
    round_ended(tmp_path, "c")
    on_board(monkeypatch, 4)
    finish_all(tmp_path)
    goal_done(tmp_path)
    assert progress.load(tmp_path)["rounds_since_gain"] == 0


def test_a_restart_on_the_same_plan_is_not_a_round(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    red_gate(monkeypatch)
    finish_all(tmp_path)
    goal_done(tmp_path)
    work.sync(tmp_path, plan.load(tmp_path))    # what `ccdirector start` does
    goal_done(tmp_path)
    assert progress.load(tmp_path)["rounds_since_gain"] == 1


def test_elapsed_reads_in_hours_and_minutes():
    assert progress.elapsed(time.time()) == "<1m"
    assert progress.elapsed(time.time() - 300) == "5m"
    assert progress.elapsed(time.time() - 7800) == "2h10m"


def test_replan_sets_the_progress_record_aside(tmp_path, monkeypatch):
    setup_run(tmp_path)
    on_board(monkeypatch, 3)
    progress.record_refusal(tmp_path)
    plan.set_aside(tmp_path)
    assert progress.load(tmp_path) == {}
    assert os.path.isfile(plan.folder(tmp_path) / "progress.json.backup")


def test_the_prompt_makes_the_director_show_the_block():
    text = session.PROMPT_FILE.read_text()
    assert "Copy the PROGRESS block into your next message" in text
    assert "with no pipe" in text


def test_the_prompt_says_what_stuck_means():
    assert "must differ in kind" in session.PROMPT_FILE.read_text()
