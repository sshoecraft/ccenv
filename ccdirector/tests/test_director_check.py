"""The done-check and the project snapshot, against the real ccproject tools in
a throwaway project. Skipped when ccenv's ccproject tools are not installed."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from ccdirector import ccproject

pytestmark = pytest.mark.skipif(
    shutil.which(ccproject.CRITERIA_TOOL) is None or shutil.which(ccproject.DEFECTS_TOOL) is None,
    reason="ccproject tools not on PATH")


def tool(root, name, *args):
    done = subprocess.run([name, *args], cwd=str(root), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr or done.stdout
    return done


def test_nothing_defines_done(tmp_path):
    assert ccproject.gate_command(tmp_path) is None
    verdict = ccproject.check(tmp_path)
    assert not verdict.done and verdict.gate == ""


def test_board_gate_decides(tmp_path):
    tool(tmp_path, ccproject.CRITERIA_TOOL, "add", "-i", "suite", "-r", "tests pass",
         "-d", "pytest")
    assert ccproject.gate_command(tmp_path)[0] == ccproject.CRITERIA_TOOL
    assert not ccproject.check(tmp_path).done
    tool(tmp_path, ccproject.CRITERIA_TOOL, "update", "suite", "-s", "PASS", "-m", "12 passed")
    assert ccproject.check(tmp_path).done


def test_build_grading_reaches_the_tool(tmp_path):
    tool(tmp_path, ccproject.CRITERIA_TOOL, "add", "-i", "suite", "-r", "tests pass",
         "-d", "pytest")
    tool(tmp_path, ccproject.CRITERIA_TOOL, "update", "suite", "-s", "PASS", "-m", "ok",
         "--build", "b1")
    assert ccproject.check(tmp_path, build="b1").done
    assert not ccproject.check(tmp_path, build="b2").done


def test_queue_alone_uses_the_defects_gate(tmp_path):
    tool(tmp_path, ccproject.DEFECTS_TOOL, "add", "-m", "it crashes", "--id", "D-CRASH")
    assert ccproject.gate_command(tmp_path)[0] == ccproject.DEFECTS_TOOL
    assert not ccproject.check(tmp_path).done
    tool(tmp_path, ccproject.DEFECTS_TOOL, "remove", "D-CRASH", "--fixed", "--why", "fixed it")
    assert ccproject.check(tmp_path).done


def test_check_from_a_subdirectory_finds_the_project(tmp_path):
    tool(tmp_path, ccproject.CRITERIA_TOOL, "add", "-i", "suite", "-r", "r", "-d", "d")
    deeper = tmp_path / "src" / "pkg"
    deeper.mkdir(parents=True)
    assert ccproject.store(deeper) == tmp_path / ".ccproject"
    assert ccproject.gate_command(deeper) is not None


def test_snapshot_reads_board_and_queue(tmp_path):
    tool(tmp_path, ccproject.CRITERIA_TOOL, "add", "-i", "suite", "-r", "tests pass",
         "-d", "pytest")
    tool(tmp_path, ccproject.DEFECTS_TOOL, "add", "-m", "it crashes", "--id", "D-CRASH")
    shot = ccproject.snapshot(tmp_path)
    assert shot.has_board and shot.has_queue
    assert shot.board_ids == frozenset({"suite"})
    assert "suite" in shot.board_text
    assert "D-CRASH" in shot.queue_text


def test_snapshot_of_a_bare_project(tmp_path):
    shot = ccproject.snapshot(tmp_path)
    assert not shot.has_board and not shot.has_queue
    assert not (tmp_path / ".ccproject").exists(), "reading must not create the store"
