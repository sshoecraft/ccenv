"""The goal-finder's answer checks: types, consistency, and agreement with the
project's own records."""
from __future__ import annotations

from pathlib import Path

import pytest

from ccdirector import ccproject, goalfinder
from ccdirector.goalfinder import GoalError


def answer(**changes) -> dict:
    base = {"found": True, "goal": "Make the board pass.", "source": "scan",
            "criteria": {"existing": [], "proposed": [
                {"id": "tests_pass", "requirement": "The test suite passes.",
                 "detector": "python3 -m pytest tests/", "why": "README.md:12"}]},
            "evidence": ["README.md:12"], "reason": "no board or queue"}
    base.update(changes)
    return base


def bare() -> ccproject.Snapshot:
    return ccproject.Snapshot(store=None)


def with_board(tmp_path: Path, ids) -> ccproject.Snapshot:
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "criteria.json").write_text('{"criteria": []}')
    return ccproject.Snapshot(store=store, board_ids=frozenset(ids))


def test_scan_answer_validates():
    goal = goalfinder.validate(answer(), bare())
    assert goal.found and goal.source == "scan"
    assert goal.proposed[0]["id"] == "tests_pass"


def test_string_false_is_not_a_bool():
    with pytest.raises(GoalError, match="true or false"):
        goalfinder.validate(answer(found="false"), bare())


def test_not_found_needs_source_none_and_a_reason():
    with pytest.raises(GoalError, match="expected 'none'"):
        goalfinder.validate(answer(found=False, source="scan"), bare())
    with pytest.raises(GoalError, match="no reason"):
        goalfinder.validate(answer(found=False, source="none", reason=" "), bare())
    goal = goalfinder.validate(answer(found=False, source="none", goal=""), bare())
    assert not goal.found


def test_board_source_without_a_board_is_refused():
    with pytest.raises(GoalError, match="no criteria board"):
        goalfinder.validate(answer(source="board"), bare())


def test_existing_ids_must_be_on_the_board(tmp_path):
    shot = with_board(tmp_path, {"a", "b"})
    ok = answer(source="board", criteria={"existing": ["a"], "proposed": []})
    assert goalfinder.validate(ok, shot).existing == ["a"]
    bad = answer(source="board", criteria={"existing": ["a", "zz"], "proposed": []})
    with pytest.raises(GoalError, match="zz"):
        goalfinder.validate(bad, shot)


def test_proposed_ids_must_be_new_and_usable(tmp_path):
    shot = with_board(tmp_path, {"tests_pass"})
    with pytest.raises(GoalError, match="already taken"):
        goalfinder.validate(answer(), shot)
    spaced = answer(criteria={"existing": [], "proposed": [
        {"id": "has space", "requirement": "r", "detector": "d", "why": "w"}]})
    with pytest.raises(GoalError, match="usable"):
        goalfinder.validate(spaced, bare())


def test_scan_without_criteria_defines_nothing():
    with pytest.raises(GoalError, match="nothing would define done"):
        goalfinder.validate(answer(criteria={"existing": [], "proposed": []}), bare())


def test_prompt_says_when_records_are_absent():
    text = goalfinder.build_prompt(Path("/p"), bare())
    assert "no criteria board" in text and "no defect queue" in text
