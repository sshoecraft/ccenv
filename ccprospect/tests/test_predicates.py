"""Predicate validation, creation checks (already-true refusal, baseline
stamping), probe bounds, and stateless evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ccprospect import predicates
from ccprospect.predicates import PredicateError
from ccprospect.util import to_iso

NOW = datetime.now(timezone.utc)


def iso(**delta) -> str:
    return to_iso(NOW + timedelta(**delta))


# -- validate -------------------------------------------------------------------

def test_validate_rejects_unknown_type():
    with pytest.raises(PredicateError, match="unknown predicate type"):
        predicates.validate({"type": "bar_high_gte"})


def test_validate_rejects_non_dict():
    with pytest.raises(PredicateError):
        predicates.validate("at 3pm")


def test_validate_at_requires_time():
    with pytest.raises(PredicateError, match="requires a 'time'"):
        predicates.validate({"type": "at"})
    with pytest.raises(PredicateError, match="unparseable"):
        predicates.validate({"type": "at", "time": "next tuesday"})


def test_validate_path_types_require_path():
    for ptype in ("path_exists", "path_changed"):
        with pytest.raises(PredicateError, match="requires a 'path'"):
            predicates.validate({"type": ptype})


def test_validate_cmd_requires_run_and_bounds_timeout():
    with pytest.raises(PredicateError, match="requires a 'run'"):
        predicates.validate({"type": "cmd_ok"})
    with pytest.raises(PredicateError, match="timeout"):
        predicates.validate({"type": "cmd_ok", "run": "true", "timeout": 60})
    with pytest.raises(PredicateError, match="min_interval"):
        predicates.validate({"type": "cmd_ok", "run": "true", "min_interval": -5})
    clean = predicates.validate({"type": "cmd_ok", "run": "true"})
    assert clean["timeout"] == predicates.PROBE_TIMEOUT_DEFAULT
    assert clean["min_interval"] == predicates.DEFAULT_MIN_INTERVAL


def test_validate_cmd_match_regex():
    with pytest.raises(PredicateError, match="requires a 'regex'"):
        predicates.validate({"type": "cmd_match", "run": "true"})
    with pytest.raises(PredicateError, match="invalid regex"):
        predicates.validate({"type": "cmd_match", "run": "true", "regex": "("})


# -- creation_check --------------------------------------------------------------

def test_creation_refuses_past_at(tmp_path):
    with pytest.raises(PredicateError, match="already true"):
        predicates.creation_check({"type": "at", "time": iso(hours=-1)},
                                  base_dir=tmp_path, now=NOW)
    predicates.creation_check({"type": "at", "time": iso(hours=1)},
                              base_dir=tmp_path, now=NOW)


def test_creation_refuses_existing_path(tmp_path):
    target = tmp_path / "present.txt"
    target.write_text("here")
    with pytest.raises(PredicateError, match="already exists"):
        predicates.creation_check({"type": "path_exists", "path": "present.txt"},
                                  base_dir=tmp_path, now=NOW)
    # negate watches for disappearance — requires the path to exist now
    predicates.creation_check({"type": "path_exists", "path": "present.txt", "negate": True},
                              base_dir=tmp_path, now=NOW)
    with pytest.raises(PredicateError, match="already absent"):
        predicates.creation_check({"type": "path_exists", "path": "gone.txt", "negate": True},
                                  base_dir=tmp_path, now=NOW)


def test_creation_stamps_path_changed_baseline(tmp_path):
    target = tmp_path / "watched.cfg"
    target.write_text("v1")
    out = predicates.creation_check({"type": "path_changed", "path": "watched.cfg"},
                                    base_dir=tmp_path, now=NOW)
    assert len(out["baseline"]) == 64
    with pytest.raises(PredicateError, match="missing or unreadable"):
        predicates.creation_check({"type": "path_changed", "path": "no-such.cfg"},
                                  base_dir=tmp_path, now=NOW)


def test_creation_refuses_already_true_probes(tmp_path):
    with pytest.raises(PredicateError, match="already exits 0"):
        predicates.creation_check(
            predicates.validate({"type": "cmd_ok", "run": "true"}),
            base_dir=tmp_path, now=NOW)
    with pytest.raises(PredicateError, match="already exits nonzero"):
        predicates.creation_check(
            predicates.validate({"type": "cmd_fail", "run": "false"}),
            base_dir=tmp_path, now=NOW)
    with pytest.raises(PredicateError, match="already matches"):
        predicates.creation_check(
            predicates.validate({"type": "cmd_match", "run": "echo deployed", "regex": "deploy"}),
            base_dir=tmp_path, now=NOW)
    # the inverse cases establish a clean baseline
    predicates.creation_check(
        predicates.validate({"type": "cmd_ok", "run": "false"}), base_dir=tmp_path, now=NOW)
    predicates.creation_check(
        predicates.validate({"type": "cmd_fail", "run": "true"}), base_dir=tmp_path, now=NOW)


def test_probe_timeout_is_recorded_not_raised(tmp_path):
    probe = predicates.run_probe("sleep 3", 1, tmp_path)
    assert probe["exit"] is None
    assert "timed out" in probe["error"]
    with pytest.raises(PredicateError, match="could not establish a baseline"):
        predicates.creation_check(
            predicates.validate({"type": "cmd_ok", "run": "sleep 3", "timeout": 1}),
            base_dir=tmp_path, now=NOW)


# -- evaluate ---------------------------------------------------------------------

def kw(tmp_path, **over):
    base = {"base_dir": tmp_path, "now": NOW,
            "created_at": iso(hours=-1), "at_session_start": False}
    base.update(over)
    return base


def test_evaluate_at(tmp_path):
    fired, observed, ran = predicates.evaluate(
        {"type": "at", "time": iso(minutes=5)}, **kw(tmp_path))
    assert not fired and not ran
    fired, observed, ran = predicates.evaluate(
        {"type": "at", "time": iso(minutes=-5)}, **kw(tmp_path))
    assert fired and observed["time"] and not ran


def test_evaluate_session_start_only_at_session_start(tmp_path):
    pred = {"type": "session_start"}
    fired, _, _ = predicates.evaluate(pred, **kw(tmp_path, at_session_start=False))
    assert not fired
    fired, observed, _ = predicates.evaluate(pred, **kw(tmp_path, at_session_start=True))
    assert fired and observed["session_start"]
    # not in the very moment of creation
    fired, _, _ = predicates.evaluate(pred, **kw(tmp_path, at_session_start=True,
                                                 created_at=to_iso(NOW)))
    assert not fired


def test_evaluate_path_exists_and_negate(tmp_path):
    pred = {"type": "path_exists", "path": "appears.txt"}
    fired, _, _ = predicates.evaluate(pred, **kw(tmp_path))
    assert not fired
    (tmp_path / "appears.txt").write_text("now")
    fired, observed, _ = predicates.evaluate(pred, **kw(tmp_path))
    assert fired and observed["exists"] is True

    gone = {"type": "path_exists", "path": "appears.txt", "negate": True}
    fired, _, _ = predicates.evaluate(gone, **kw(tmp_path))
    assert not fired
    (tmp_path / "appears.txt").unlink()
    fired, observed, _ = predicates.evaluate(gone, **kw(tmp_path))
    assert fired and observed["exists"] is False


def test_evaluate_path_changed_including_deletion(tmp_path):
    target = tmp_path / "watched.cfg"
    target.write_text("v1")
    pred = predicates.creation_check({"type": "path_changed", "path": "watched.cfg"},
                                     base_dir=tmp_path, now=NOW)
    fired, _, _ = predicates.evaluate(pred, **kw(tmp_path))
    assert not fired
    target.write_text("v2")
    fired, observed, _ = predicates.evaluate(pred, **kw(tmp_path))
    assert fired and observed["current"] != observed["baseline"]
    target.unlink()
    fired, observed, _ = predicates.evaluate(pred, **kw(tmp_path))
    assert fired and observed["current"] == "missing"


def test_evaluate_cmd_predicates(tmp_path):
    flag = tmp_path / "release.flag"
    pred = predicates.validate({"type": "cmd_ok", "run": "test -f release.flag"})
    fired, _, ran = predicates.evaluate(pred, **kw(tmp_path))
    assert not fired and ran
    flag.write_text("out")
    fired, observed, ran = predicates.evaluate(pred, **kw(tmp_path))
    assert fired and observed["exit"] == 0 and ran

    match = predicates.validate({"type": "cmd_match", "run": "cat release.flag",
                                 "regex": "o.t"})
    fired, observed, _ = predicates.evaluate(match, **kw(tmp_path))
    assert fired and observed["matched"] == "out"


def test_evaluate_cmd_timeout_never_fires(tmp_path):
    pred = predicates.validate({"type": "cmd_fail", "run": "sleep 3", "timeout": 1})
    fired, observed, ran = predicates.evaluate(pred, **kw(tmp_path))
    assert not fired and ran
    assert "timed out" in observed["probe_error"]
