"""Work-plan validation and the files `plan submit` writes."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ccdirector import cli, goalfinder, plan, session

GOOD = {
    "goal": "Make every criterion green.",
    "source": "board",
    "gate": "ccproject_criteria.py --gate",
    "criteria": ["coherency", "resize"],
    "items": [
        {"id": "lock-order", "what": "Fix the lock order in the DLM.", "serves": ["coherency"],
         "priority": 1, "section": "dlm", "depends_on": [], "paths": ["src/dlm/**"],
         "resources": [], "check": "tests/criteria/cache_coherency.sh", "size": "M"},
        {"id": "resize-grow", "what": "Grow the journal on resize.", "serves": ["resize"],
         "priority": 2, "section": "journal", "depends_on": ["lock-order"],
         "paths": ["src/journal/*.c"], "resources": ["vm group a"],
         "check": "tests/criteria/online_resize.sh", "check_timeout": 400, "size": "L"},
    ],
    "risks": [],
}


def test_good_plan_has_no_problems():
    assert plan.problems(GOOD) == []


def test_check_is_required_and_bounded():
    bad = copy.deepcopy(GOOD)
    del bad["items"][0]["check"]
    bad["items"][1]["check_timeout"] = plan.MAX_CHECK_SECONDS + 1
    found = plan.problems(bad)
    assert any("items[0].check" in p for p in found)
    assert any("check_timeout" in p for p in found)


def test_interface_and_section_types():
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["interface"] = "yes"
    bad["items"][1]["section"] = ""
    found = plan.problems(bad)
    assert any("interface" in p for p in found)
    assert any("section" in p for p in found)


def test_unknown_dependency():
    bad = copy.deepcopy(GOOD)
    bad["items"][1]["depends_on"] = ["ghost"]
    assert any("unknown item 'ghost'" in p for p in plan.problems(bad))


def test_dependency_cycle():
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["depends_on"] = ["resize-grow"]
    assert any("cycle" in p for p in plan.problems(bad))


def test_priority_must_be_a_real_integer():
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["priority"] = True
    assert any("priority" in p for p in plan.problems(bad))


def test_serves_must_name_plan_criteria():
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["serves"] = ["unlisted"]
    assert any("'unlisted'" in p for p in plan.problems(bad))


def test_write_and_load(tmp_path):
    json_path, md_path = plan.write(tmp_path, GOOD)
    assert plan.load(tmp_path) == GOOD
    text = md_path.read_text()
    assert "`lock-order`" in text and "| dlm |" in text
    assert "tests/criteria/online_resize.sh" in text and "400 s" in text
    assert not list(json_path.parent.glob("*.json.*")), "temp file left behind"


def test_a_plan_from_before_checks_still_renders(tmp_path):
    old = copy.deepcopy(GOOD)
    for entry in old["items"]:
        del entry["check"]
    old["allocation"] = [{"worker": "worker-1", "items": ["lock-order"], "owns": ["src/**"]}]
    text = plan.render(old)
    assert "plan predates checks" in text
    assert plan.problems(old), "but it is not an acceptable plan"


def submit(root, body):
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", "plan", "submit",
                           "--project", str(root)], input=body, capture_output=True, text=True)


def test_submit_accepts_and_refuses(tmp_path):
    folder = plan.folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / "goal.json").write_text(json.dumps({"workers": 2}))
    done = submit(tmp_path, json.dumps(GOOD))
    assert done.returncode == 0 and done.stdout.startswith("ACCEPTED")
    bad = copy.deepcopy(GOOD)
    bad["items"] = []
    done = submit(tmp_path, json.dumps(bad))
    assert done.returncode == 1 and done.stdout.startswith("REFUSED")
    assert plan.load(tmp_path) == GOOD, "a refused plan must not replace the accepted one"
    done = submit(tmp_path, "{not json")
    assert done.returncode == 1 and "not valid JSON" in done.stdout


def plan_done(root, run_id):
    import os
    env = {k: v for k, v in os.environ.items() if k != "CCLOOP_RUN_ID"}
    if run_id:
        env["CCLOOP_RUN_ID"] = run_id
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", "plan", "done",
                           "--project", str(root)], capture_output=True, text=True, env=env)


def test_plan_done_writes_the_marker_only_for_an_accepted_current_plan(tmp_path):
    run_dir = tmp_path / ".ccloop" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    marker = run_dir / "criteria-met"
    folder = plan.folder(tmp_path)
    folder.mkdir(parents=True)
    goal = folder / "goal.json"
    goal.write_text(json.dumps({"goal": "Ship it.", "workers": 2}))

    done = plan_done(tmp_path, "r1")
    assert done.returncode == 1 and "no plan" in done.stdout and not marker.exists()

    plan.write(tmp_path, GOOD)                  # no record of its goal: from before the stamp
    done = plan_done(tmp_path, "r1")
    assert done.returncode == 1 and "current goal" in done.stdout and not marker.exists()

    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "An earlier goal."})
    done = plan_done(tmp_path, "r1")
    assert done.returncode == 1 and "current goal" in done.stdout and not marker.exists()

    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "Ship it."})
    assert plan_done(tmp_path, None).returncode == 1 and not marker.exists()
    assert plan_done(tmp_path, "nope").returncode == 1 and not marker.exists()

    done = plan_done(tmp_path, "r1")
    assert done.returncode == 0, done.stdout
    assert marker.read_text() == "YES\n"


def directive(text):
    return goalfinder.Goal(found=True, goal=text, source="directive", reason="given")


def test_submit_records_the_goal_and_the_director_cannot_set_it(tmp_path):
    cli.save_goal(tmp_path, directive("Ship it."), 1)
    reworded = {**GOOD, "goal": "The director's wording.", plan.WRITTEN_FOR: "Forged."}
    assert submit(tmp_path, json.dumps(reworded)).returncode == 0
    stored = plan.load(tmp_path)
    assert stored[plan.WRITTEN_FOR] == "Ship it." and stored["goal"] == "The director's wording."


def test_restarting_on_the_same_goal_needs_no_resubmit(tmp_path):
    run_dir = tmp_path / ".ccloop" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    cli.save_goal(tmp_path, directive("Ship it."), 1)
    assert submit(tmp_path, json.dumps(GOOD)).returncode == 0
    cli.save_goal(tmp_path, directive("Ship it."), 3)   # a second `ccdirector start`
    done = plan_done(tmp_path, "r1")
    assert done.returncode == 0, done.stdout
    assert (run_dir / "criteria-met").read_text() == "YES\n"


def test_restarting_on_a_new_goal_requires_a_submit(tmp_path):
    (tmp_path / ".ccloop" / "runs" / "r1").mkdir(parents=True)
    cli.save_goal(tmp_path, directive("Ship it."), 1)
    assert submit(tmp_path, json.dumps(GOOD)).returncode == 0
    cli.save_goal(tmp_path, directive("Something else."), 1)
    assert plan_done(tmp_path, "r1").returncode == 1
    assert submit(tmp_path, json.dumps(GOOD)).returncode == 0
    assert plan_done(tmp_path, "r1").returncode == 0


def test_planning_task_asks_for_a_repair_of_an_unusable_plan_only(tmp_path):
    broken = copy.deepcopy(GOOD)
    del broken["items"][0]["check"]
    plan.write(tmp_path, broken)
    task = cli.plan_task(tmp_path, directive("Ship it."), 1, None)
    assert "repair exactly these problems" in task and "Do not write a new plan" in task
    assert "items[0].check" in task


def test_planning_task_says_nothing_when_no_plan_is_on_file(tmp_path):
    assert "The plan on file" not in cli.plan_task(tmp_path, directive("Ship it."), 1, None)


class StartRecord:
    """What `ccdirector start` actually did, with every session stubbed out."""

    def __init__(self, monkeypatch, plan_on_exit=None):
        self.planned, self.steered, self.workers, self.found = [], [], [], []

        def planning(root, goal, args):
            self.planned.append((goal.goal, plan.load(root)))
            if plan_on_exit:
                plan.write(root, plan_on_exit)
            return 0

        def steering(root, goal, args):
            self.steered.append(goal.goal)
            return 0

        def find(root, **kwargs):
            self.found.append(root)
            return directive("The goal-finder's goal.")

        record = self

        class Supervisor:
            def __init__(self, root, options):
                pass

            def start(self):
                record.workers.append("started")

            def stop(self):
                pass

        monkeypatch.setattr(cli, "planning", planning)
        monkeypatch.setattr(cli, "steering", steering)
        monkeypatch.setattr(cli.goalfinder, "find", find)
        monkeypatch.setattr(cli.workers, "Supervisor", Supervisor)
        monkeypatch.setattr(cli.conflicts, "ccteam", lambda root: [])


def start(root, *flags):
    return cli.cmd_start(cli.build_parser().parse_args(["start", str(root), *flags]))


def test_start_uses_the_plan_on_file_without_planning_or_the_goal_finder(tmp_path, monkeypatch):
    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "Ship it."})
    did = StartRecord(monkeypatch)
    assert start(tmp_path) == 0
    assert did.planned == [] and did.found == []
    assert did.workers == ["started"] and did.steered == ["Ship it."]


def test_a_plan_from_before_the_record_is_used_under_its_own_goal(tmp_path, monkeypatch):
    plan.write(tmp_path, GOOD)
    did = StartRecord(monkeypatch)
    assert start(tmp_path) == 0
    assert did.planned == [] and did.steered == [GOOD["goal"]]


def test_a_different_goal_alone_keeps_the_plan_and_says_so(tmp_path, monkeypatch, capsys):
    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "Ship it."})
    did = StartRecord(monkeypatch)
    assert start(tmp_path, "--goal", "Something else.") == 0
    assert did.planned == [] and did.steered == ["Ship it."]
    assert "--replan" in capsys.readouterr().err


def test_replan_sets_the_old_plan_aside_and_plans_the_new_goal(tmp_path, monkeypatch):
    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "Ship it."})
    did = StartRecord(monkeypatch, plan_on_exit=GOOD)
    assert start(tmp_path, "--replan", "--goal", "Something else.") == 0
    assert did.planned == [("Something else.", None)]
    assert did.steered == ["Something else."]
    assert json.loads((plan.folder(tmp_path) / "plan.json.backup").read_text())[
        plan.WRITTEN_FOR] == "Ship it."
    assert (plan.folder(tmp_path) / "plan.md.backup").is_file()


def test_an_unusable_plan_gets_a_planning_run_under_its_own_goal(tmp_path, monkeypatch):
    broken = copy.deepcopy(GOOD)
    del broken["items"][0]["check"]
    plan.write(tmp_path, broken)
    did = StartRecord(monkeypatch, plan_on_exit=GOOD)
    assert start(tmp_path) == 0
    assert [goal for goal, on_file in did.planned] == [GOOD["goal"]] and did.found == []


def test_with_no_plan_the_goal_finder_and_planning_run(tmp_path, monkeypatch):
    did = StartRecord(monkeypatch, plan_on_exit=GOOD)
    assert start(tmp_path) == 0
    assert did.found == [tmp_path.resolve()]
    assert did.planned == [("The goal-finder's goal.", None)]


def test_plan_only_after_reusing_a_plan_starts_no_workers(tmp_path, monkeypatch):
    plan.write(tmp_path, {**GOOD, plan.WRITTEN_FOR: "Ship it."})
    did = StartRecord(monkeypatch)
    assert start(tmp_path, "--plan-only") == 0
    assert did.workers == [] and did.steered == []


def test_the_director_prompt_forbids_asking_the_user():
    text = session.PROMPT_FILE.read_text()
    assert "never ask the user a question" in text
    assert "what you need from the user" not in text


def director_args():
    return argparse.Namespace(workers=1, build=None, headless=False, model=None, cutoff=None,
                              effort=None)


def must_not_resume(*args, **kwargs):
    raise AssertionError("the user stopped this run; it must not be reopened")


@pytest.mark.parametrize("code", [3, 130])
@pytest.mark.parametrize("on_file", [None, GOOD])
def test_planning_ends_when_the_user_stops_it(tmp_path, monkeypatch, code, on_file):
    if on_file:
        plan.write(tmp_path, on_file)   # a valid plan must not read as "planning finished"
    monkeypatch.setattr(cli.session, "start", lambda *a, **k: session.Run(code, tmp_path))
    monkeypatch.setattr(cli.session, "resume", must_not_resume)
    assert cli.planning(tmp_path, directive("Ship it."), director_args()) == code


@pytest.mark.parametrize("code", [3, 130])
def test_steering_ends_when_the_user_stops_it(tmp_path, monkeypatch, code):
    monkeypatch.setattr(cli.session, "start", lambda *a, **k: session.Run(code, tmp_path))
    monkeypatch.setattr(cli.session, "resume", must_not_resume)
    monkeypatch.setattr(cli.ccproject, "check", lambda *a: SimpleNamespace(done=False, output="red"))
    assert cli.steering(tmp_path, directive("Ship it."), director_args()) == code


def test_a_run_that_ended_on_its_own_without_a_plan_is_still_reopened(tmp_path, monkeypatch):
    reopened = []

    def resume(root, run, feedback, **flags):
        reopened.append(feedback)
        return session.Run(3, tmp_path)

    monkeypatch.setattr(cli.session, "start", lambda *a, **k: session.Run(0, tmp_path))
    monkeypatch.setattr(cli.session, "resume", resume)
    assert cli.planning(tmp_path, directive("Ship it."), director_args()) == 3
    assert len(reopened) == 1 and "no accepted work plan" in reopened[0]


def test_plan_done_refuses_a_stored_plan_that_no_longer_validates(tmp_path):
    (tmp_path / ".ccloop" / "runs" / "r1").mkdir(parents=True)
    folder = plan.folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / "goal.json").write_text(json.dumps({"workers": 1}))
    stale = copy.deepcopy(GOOD)
    del stale["items"][0]["check"]               # e.g. a plan from before checks existed
    (folder / "plan.json").write_text(json.dumps(stale))
    done = plan_done(tmp_path, "r1")
    assert done.returncode == 1 and "problem" in done.stdout
    assert not (tmp_path / ".ccloop" / "runs" / "r1" / "criteria-met").exists()
