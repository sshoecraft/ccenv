"""The per-file edit lock workers' hooks take around each Edit/Write."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ccdirector import editlock

#: The source tree, so the subprocesses run this code, not an installed copy.
REPO = str(Path(__file__).resolve().parents[1] / "src")


def test_acquire_release_and_contention(tmp_path):
    target = tmp_path / "src.c"
    assert editlock.acquire(tmp_path, target, "w1") == (True, "")
    ok, holder = editlock.acquire(tmp_path, target, "w2", wait=0.2)
    assert not ok and holder == "w1"
    assert editlock.release(tmp_path, target, "w2") is False, "only the owner releases"
    assert editlock.release(tmp_path, target, "w1") is True
    assert editlock.acquire(tmp_path, target, "w2", wait=0.2) == (True, "")


def test_same_owner_refreshes_its_lease(tmp_path):
    target = tmp_path / "src.c"
    editlock.acquire(tmp_path, target, "w1")
    assert editlock.acquire(tmp_path, target, "w1", wait=0) == (True, "")


def test_abandoned_lease_is_broken(tmp_path):
    target = tmp_path / "src.c"
    editlock.folder(tmp_path).mkdir(parents=True)
    editlock.lease_path(tmp_path, target).write_text(json.dumps(
        {"owner": "gone", "time": time.time() - editlock.LEASE_SECONDS - 1, "path": str(target)}))
    assert editlock.acquire(tmp_path, target, "w1", wait=0.5) == (True, "")
    log = (tmp_path / ".ccdirector" / "locks.log").read_text()
    assert '"op": "break"' in log


RACER = r"""
import sys, time
from pathlib import Path
from ccdirector import editlock
root, owner, rounds = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
target = root / "counter"
for _ in range(rounds):
    ok, holder = editlock.acquire(root, target, owner, wait=30)
    assert ok, holder
    value = int(target.read_text())
    time.sleep(0.001)                 # widen the read-modify-write window
    target.write_text(str(value + 1))
    assert editlock.release(root, target, owner)
"""


def test_real_processes_never_lose_an_update(tmp_path):
    """Four processes increment one file 40 times each under the lock: a lost update
    would leave the count short."""
    (tmp_path / "counter").write_text("0")
    procs = [subprocess.Popen([sys.executable, "-c", RACER, str(tmp_path), f"w{n}", "40"])
             for n in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    assert (tmp_path / "counter").read_text() == "160"


def run_hook(stage, payload, root, worker="w1"):
    env = {k: v for k, v in os.environ.items() if k not in ("CCDIRECTOR_ROOT", "CCDIRECTOR_WORKER")}
    env["PYTHONPATH"] = REPO
    if root is not None:
        env["CCDIRECTOR_ROOT"] = str(root)
    if worker is not None:
        env["CCDIRECTOR_WORKER"] = worker
    return subprocess.run([sys.executable, "-m", "ccdirector.cli", "hook", stage],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)


def test_hook_takes_and_releases_the_lease(tmp_path):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.c")}}
    assert run_hook("pre", payload, tmp_path).returncode == 0
    lease = editlock.lease_path(tmp_path, Path(os.path.realpath(tmp_path / "a.c")))
    assert json.loads(lease.read_text())["owner"] == "w1"
    assert run_hook("post", payload, tmp_path, worker="w2").returncode == 0
    assert lease.exists(), "another worker's post must not release w1's lease"
    assert run_hook("post", payload, tmp_path).returncode == 0
    assert not lease.exists()


def test_hook_refuses_while_another_worker_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(editlock, "WAIT_SECONDS", 0.3)
    target = Path(os.path.realpath(tmp_path / "a.c"))
    editlock.acquire(tmp_path, target, "w1")
    monkeypatch.setenv("CCDIRECTOR_ROOT", str(tmp_path))
    monkeypatch.setenv("CCDIRECTOR_WORKER", "w2")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(target)}}
    assert editlock.pre(payload) == 2


def test_hook_ignores_paths_outside_the_project_and_other_tools(tmp_path):
    outside = {"tool_name": "Edit", "tool_input": {"file_path": "/etc/hosts"}}
    assert run_hook("pre", outside, tmp_path).returncode == 0
    assert run_hook("pre", {"tool_name": "Bash", "tool_input": {"command": "ls"}},
                    tmp_path).returncode == 0
    assert not editlock.folder(tmp_path).exists()


def test_notebook_path_is_locked(tmp_path):
    payload = {"tool_name": "NotebookEdit",
               "tool_input": {"notebook_path": str(tmp_path / "n.ipynb")}}
    assert editlock.targets(payload, Path(os.path.realpath(tmp_path)))


def test_hook_fails_closed_without_its_identity(tmp_path):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.c")}}
    done = run_hook("pre", payload, None, worker=None)
    assert done.returncode == 2 and "edit refused" in done.stderr
