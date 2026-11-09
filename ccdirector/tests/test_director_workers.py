"""What a worker session is launched with, and the worker-facing commands."""
from __future__ import annotations

import json
import os
import subprocess
import sys
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
    assert ccdirector(tmp_path, "item", "next", worker="worker-2").stdout.startswith("NONE")
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


def test_item_commands_need_a_worker_name(tmp_path):
    done = ccdirector(tmp_path, "item", "next", worker=None)
    assert done.returncode != 0 and "CCDIRECTOR_WORKER" in done.stderr


def test_wait_reports_stalled_and_goal_stuck_is_gated(tmp_path):
    plan.write(tmp_path, PLAN)
    work.sync(tmp_path, PLAN)
    (tmp_path / ".ccloop" / "runs" / "d1").mkdir(parents=True)
    refused = ccdirector(tmp_path, "goal-stuck", "the", "rig", "is", "down", worker=None,
                         run_id="d1")
    assert refused.returncode == 1 and "still in progress or can still be handed out" in refused.stdout
    ccdirector(tmp_path, "item", "next")
    ccdirector(tmp_path, "item", "park", "a", "--reason", "rig down")
    waited = ccdirector(tmp_path, "wait", "--timeout", "1", worker=None)
    assert "item_parked" in waited.stdout and "STALLED" in waited.stdout
    assert waited.stdout.startswith("PROGRESS items 0/"), "the screen keeps only the top lines"
    stuck = ccdirector(tmp_path, "goal-stuck", "the", "rig", "is", "down", worker=None,
                       run_id="d1")
    assert stuck.returncode == 0 and stuck.stdout.startswith("STOPPED")
    assert json.loads((plan.folder(tmp_path) / "stuck.json").read_text())["reason"] == \
        "the rig is down"
    assert (tmp_path / ".ccloop" / "runs" / "d1" / "criteria-met").exists()


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
