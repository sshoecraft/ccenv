"""ccproject_criteria.py, driven as a subprocess in a throwaway project directory."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CRITERIA = SCRIPTS / "ccproject_criteria.py"
DEFECTS = SCRIPTS / "ccproject_defects.py"


def run(cwd, *argv, check=True, script=CRITERIA):
    result = subprocess.run([sys.executable, str(script), *argv], cwd=cwd,
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"exit {result.returncode}: {result.stderr}")
    return result


def board(project):
    return json.loads((project / ".ccproject" / "criteria.json").read_text())


def row(project, criterion):
    return next(r for r in board(project)["criteria"] if r["id"] == criterion)


def test_empty_board_fails_the_gate(tmp_path):
    result = run(tmp_path, "--gate", check=False)
    assert result.returncode == 1 and "no criteria defined" in result.stdout


def test_add_update_and_verdict(tmp_path):
    run(tmp_path, "add", "-i", "boots", "-r", "it boots", "-d", "tests/boot.sh")
    run(tmp_path, "add", "-i", "fast", "-r", "it is fast", "-d", "tests/bench.py")
    assert row(tmp_path, "boots")["status"] == "NOT STARTED"
    run(tmp_path, "update", "boots", "-s", "pass", "-m", "booted in 3s")
    out = run(tmp_path, "--gate", check=False)
    assert out.returncode == 1 and "1 of 2 criteria green" in out.stdout
    run(tmp_path, "update", "fast", "-s", "PASS")
    out = run(tmp_path, "--gate")
    assert "every criterion green (2)" in out.stdout


def test_unknown_status_refused(tmp_path):
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    result = run(tmp_path, "update", "c", "-s", "GREENISH", check=False)
    assert result.returncode == 1 and "unknown status" in result.stderr


def test_result_and_spec_cannot_combine(tmp_path):
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    assert run(tmp_path, "update", "c", "-s", "PASS", "-r", "new", check=False).returncode == 1
    run(tmp_path, "update", "c", "-r", "new requirement")
    assert row(tmp_path, "c")["requirement"] == "new requirement"


def test_new_result_pushes_history_and_clears_stale_fields(tmp_path):
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    run(tmp_path, "update", "c", "-s", "FAIL", "-m", "3 errors", "--evidence", "log1")
    run(tmp_path, "update", "c", "-s", "PASS", "-m", "clean")
    current = row(tmp_path, "c")
    assert current["status"] == "PASS" and current["measured"] == "clean"
    assert "evidence" not in current
    assert current["history"][0]["status"] == "FAIL"
    assert current["history"][0]["evidence"] == "log1"


def test_matrix_row_partial_fail_then_pass_with_axis_alias(tmp_path):
    run(tmp_path, "axis", "class", "warrior", "mage", "priest")
    run(tmp_path, "add", "-i", "levels", "-r", "keeps levelling", "-d", "d", "--per-cell")
    run(tmp_path, "update", "levels", "-s", "PASS", "--class", "warrior")
    assert row(tmp_path, "levels")["status"] == "PARTIAL"
    run(tmp_path, "update", "levels", "-s", "FAIL", "--cell", "mage")
    assert row(tmp_path, "levels")["status"] == "FAIL"
    run(tmp_path, "update", "levels", "-s", "PASS", "--class", "mage")
    run(tmp_path, "update", "levels", "-s", "PASS", "--class", "priest")
    current = row(tmp_path, "levels")
    assert current["status"] == "PASS"
    assert set(current["per_class"]) == {"warrior", "mage", "priest"}
    assert "3/3 class" in run(tmp_path, "--no-colour").stdout


def test_matrix_row_values_subset(tmp_path):
    run(tmp_path, "axis", "config", "a", "b", "c")
    run(tmp_path, "add", "-i", "x", "-r", "r", "-d", "d", "--per-cell")
    run(tmp_path, "update", "x", "--values", "a", "b")
    run(tmp_path, "update", "x", "-s", "PASS", "--config", "a")
    run(tmp_path, "update", "x", "-s", "PASS", "--config", "b")
    assert row(tmp_path, "x")["status"] == "PASS"
    refused = run(tmp_path, "update", "x", "-s", "PASS", "--config", "c", check=False)
    assert refused.returncode == 1 and "not a config of x" in refused.stderr


def test_at_is_an_alias_for_cell(tmp_path):
    run(tmp_path, "axis", "config", "2/net/mesh/direct", "1/xfs")
    run(tmp_path, "add", "-i", "mounts", "-r", "r", "-d", "d", "--per-cell")
    run(tmp_path, "update", "mounts", "-s", "PASS", "--at", "2/net/mesh/direct")
    assert row(tmp_path, "mounts")["per_config"]["2/net/mesh/direct"]["status"] == "PASS"


def test_build_grading_reads_other_builds_as_stale(tmp_path):
    run(tmp_path, "axis", "class", "warrior", "mage")
    run(tmp_path, "add", "-i", "plain", "-r", "r", "-d", "d")
    run(tmp_path, "add", "-i", "m", "-r", "r", "-d", "d", "--per-cell")
    run(tmp_path, "update", "plain", "-s", "PASS", "--build", "b1")
    run(tmp_path, "update", "m", "-s", "PASS", "--class", "warrior", "--build", "b1")
    run(tmp_path, "update", "m", "-s", "PASS", "--class", "mage", "--build", "b2")
    assert run(tmp_path, "--gate").returncode == 0  # as recorded: all green
    on_b2 = run(tmp_path, "--gate", "--build", "b2", "--no-colour", check=False)
    assert on_b2.returncode == 1
    assert "STALE" in on_b2.stdout and "STALE: warrior" in on_b2.stdout
    assert "graded on build b2" in on_b2.stdout
    assert row(tmp_path, "plain")["status"] == "PASS"  # stored result untouched
    assert row(tmp_path, "m")["per_class"]["warrior"]["build"] == "b1"


def test_matrix_row_with_no_cells_keeps_its_set_status(tmp_path):
    run(tmp_path, "axis", "class", "warrior")
    run(tmp_path, "add", "-i", "m", "-r", "r", "-d", "d", "--per-cell")
    assert "NOT STARTED" in run(tmp_path, "--no-colour").stdout.splitlines()[0]


def test_first_cell_write_makes_a_plain_row_a_matrix_row(tmp_path):
    # smartbot marks rows class-dependent without always giving them a per_class map; its
    # harness writes --class to them anyway, and that must land, not be refused.
    run(tmp_path, "axis", "class", "warrior", "mage")
    run(tmp_path, "add", "-i", "plain", "-r", "r", "-d", "d")
    run(tmp_path, "update", "plain", "-s", "PASS", "--class", "warrior")
    current = row(tmp_path, "plain")
    assert current["per_class"]["warrior"]["status"] == "PASS"
    assert current["status"] == "PARTIAL"


def test_cell_write_needs_an_axis_and_a_declared_value(tmp_path):
    run(tmp_path, "add", "-i", "plain", "-r", "r", "-d", "d")
    assert run(tmp_path, "update", "plain", "-s", "PASS", "--cell", "x",
               check=False).returncode == 1
    run(tmp_path, "axis", "class", "warrior")
    run(tmp_path, "add", "-i", "m", "-r", "r", "-d", "d", "--per-cell")
    assert run(tmp_path, "update", "m", "-s", "PASS", "--class", "rogue",
               check=False).returncode == 1


def test_project_key_survives_every_write(tmp_path):
    run(tmp_path, "axis", "class", "warrior")
    run(tmp_path, "add", "-i", "m", "-r", "r", "-d", "d", "--per-cell")
    store = tmp_path / ".ccproject" / "criteria.json"
    data = json.loads(store.read_text())
    data["project"] = {"board": 1}
    data["criteria"][0]["project"] = {"row": 2}
    data["criteria"][0]["per_class"]["warrior"] = {"status": "FAIL", "project": {"cell": 3}}
    store.write_text(json.dumps(data))
    run(tmp_path, "update", "m", "-s", "PASS", "--class", "warrior", "-m", "ok")
    run(tmp_path, "pending", "m", "--class", "warrior", "--run-id", "R")
    run(tmp_path, "finalize", "--run-id", "R")
    run(tmp_path, "update", "m", "-r", "new requirement")
    data = board(tmp_path)
    assert data["project"] == {"board": 1}
    assert data["criteria"][0]["project"] == {"row": 2}
    assert data["criteria"][0]["per_class"]["warrior"]["project"] == {"cell": 3}


def test_blocking_defects_are_a_line_of_the_verdict(tmp_path):
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    run(tmp_path, "update", "c", "-s", "PASS")
    assert run(tmp_path, "--gate").returncode == 0  # no queue: criteria alone
    run(tmp_path, "add", "-m", "Data loss on resume", "-s", "critical", script=DEFECTS)
    gated = run(tmp_path, "--gate", "--no-colour", check=False)
    assert gated.returncode == 1
    assert "no blocking defects  1 blocking: D-DATA-LOSS-ON-RESUME" in gated.stdout
    assert "1 of 2 criteria green" in gated.stdout
    run(tmp_path, "gate-require", "impact", "integrity", script=DEFECTS)  # unrated: passes
    out = run(tmp_path, "--gate", "--no-colour").stdout
    assert "1 open, none blocking" in out and "every criterion green (2)" in out
    run(tmp_path, "gate-require", "impact", script=DEFECTS)
    store = tmp_path / ".ccproject" / "criteria.json"
    data = json.loads(store.read_text())
    data["defects_gate"] = False
    store.write_text(json.dumps(data))
    assert run(tmp_path, "--gate").returncode == 0


def test_board_and_queue_agree_on_which_defects_block(tmp_path):
    # The criteria script carries its own copy of the defect-gate rule; it must give the same
    # answer as the defects script on every combination of the two filters.
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    store = tmp_path / ".ccproject"
    (store / "defects.json").write_text(json.dumps({"defects": [
        {"id": "D-1", "severity": "high", "summary": "s", "impact": "integrity"},
        {"id": "D-2", "severity": "high", "summary": "s", "impact": "integrity",
         "scope": "harness"},
        {"id": "D-3", "severity": "high", "summary": "s", "impact": "noblock"},
        {"id": "D-4", "severity": "high", "summary": "s", "scope": "harness"},
        {"id": "D-5", "severity": "high", "summary": "s"}]}))
    filters = [(), (("gate-exclude", "scope", "harness"),),
               (("gate-require", "impact", "integrity", "stability"),),
               (("gate-require", "impact", "integrity", "stability"),
                ("gate-exclude", "scope", "harness"))]
    for steps in filters:
        for field in ("scope", "impact"):
            for kind in ("gate-exclude", "gate-require"):
                data = json.loads((store / "defects.json").read_text())
                if field in (data.get(kind.replace("-", "_")) or {}):
                    run(tmp_path, kind, field, script=DEFECTS)
        for step in steps:
            run(tmp_path, *step, script=DEFECTS)
        queue = run(tmp_path, "--gate", "--json", check=False, script=DEFECTS)
        expected = sorted(d["id"] for d in json.loads(queue.stdout)["defects"])
        verdict = json.loads(run(tmp_path, "--json").stdout)["verdict"]
        assert sorted(verdict["blocking_defects"]) == expected, steps


DRIVER = '''
import json

API = 1


def applies(data, row):
    return row.get("project", {}).get("applies")


def grade(data, row, value, record, build):
    # This project's bar: a cell is green only after two passes.
    if record is None or record.get("status") != "PASS":
        return None
    return "PASS" if record.get("project", {}).get("passes", 0) >= 2 else "PARTIAL"


def on_result(data, row, value, record, args):
    record.setdefault("project", {})
    if record.get("status") == "PASS":
        record["project"]["passes"] = record["project"].get("passes", 0) + 1
    log = ccproject.project_store() / "runs.jsonl"
    with log.open("a") as handle:
        handle.write(json.dumps({"row": row["id"], "value": value,
                                 "status": record.get("status")}) + "\\n")


def blocks(data, entry, args):
    at = getattr(args, "at_config", None)
    if at is None:
        return None
    return at in entry.get("project", {}).get("blocks", [])


def options(parser):
    parser.add_argument("--at-config")


def tally(data, args):
    print("rows:", len(data["criteria"]))
    return 3


def commands(sub):
    sub.add_parser("tally").set_defaults(driver_command=tally)
'''


def test_driver_hooks_grade_apply_log_and_add_commands(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "driver.py").write_text(DRIVER)
    run(tmp_path, "axis", "config", "a", "b")
    run(tmp_path, "add", "-i", "x", "-r", "r", "-d", "d", "--per-cell")
    data = board(tmp_path)
    data["criteria"][0]["project"] = {"applies": ["a"]}
    (store / "criteria.json").write_text(json.dumps(data))
    run(tmp_path, "update", "x", "-s", "PASS", "--at", "a")
    assert "PARTIAL" in run(tmp_path, "--no-colour").stdout.splitlines()[0]  # one pass of two
    run(tmp_path, "update", "x", "-s", "PASS", "--at", "a")
    assert run(tmp_path, "--gate").returncode == 0  # two passes; "b" does not apply
    assert len((store / "runs.jsonl").read_text().splitlines()) == 2
    refused = run(tmp_path, "update", "x", "-s", "PASS", "--at", "b", check=False)
    assert refused.returncode == 1  # applies() is what the row must pass, and "b" is not in it
    result = run(tmp_path, "tally", check=False)
    assert result.returncode == 3 and "rows: 1" in result.stdout


def test_driver_blocks_hook_and_option_reach_the_defect_line(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "driver.py").write_text(DRIVER)
    run(tmp_path, "add", "-i", "c", "-r", "r", "-d", "d")
    run(tmp_path, "update", "c", "-s", "PASS")
    run(tmp_path, "update", "c", "-s", "PASS")  # the fixture driver's bar is two passes
    (store / "defects.json").write_text(json.dumps({"defects": [
        {"id": "D-ONLY-ON-TWO", "severity": "high", "summary": "s",
         "project": {"blocks": ["2"]}}]}))
    assert run(tmp_path, "--gate", "--at-config", "1").returncode == 0
    assert run(tmp_path, "--gate", "--at-config", "2", check=False).returncode == 1


def test_driver_api_mismatch_and_builtin_redefinition_are_refused(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "driver.py").write_text("API = 2\n")
    result = run(tmp_path, check=False)
    assert result.returncode == 1 and "declares API = 2" in result.stderr
    (store / "driver.py").write_text(
        "API = 1\ndef commands(sub):\n    sub.add_parser('show')\n")
    result = run(tmp_path, check=False)
    assert result.returncode != 0
    (store / "driver.py").write_text("API = 1\ndef options(parser):\n"
                                     "    parser.add_argument('--gate')\n")
    result = run(tmp_path, check=False)
    assert result.returncode == 1 and "collide" in result.stderr


def test_open_defect_shows_as_blocking(tmp_path):
    run(tmp_path, "add", "-i", "stable", "-r", "never crashes", "-d", "d")
    run(tmp_path, "add", "-m", "Crashes on resume", "--criterion", "stable", script=DEFECTS)
    out = run(tmp_path, "--no-colour").stdout
    assert "blocked by D-CRASHES-ON-RESUME" in out
    assert "D-CRASHES-ON-RESUME" in run(tmp_path, "show", "stable").stdout


def test_gaps_reports_missing_detector_scripts(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "real.py").write_text("")
    run(tmp_path, "add", "-i", "ok", "-r", "r", "-d", "tests/real.py --fast")
    run(tmp_path, "add", "-i", "gone", "-r", "r", "-d", "tests/missing.sh")
    run(tmp_path, "add", "-i", "blank", "-r", "r", "-d", "")
    out = run(tmp_path, "--gaps").stdout
    assert "gone: detector tests/missing.sh does not exist" in out
    assert "blank: no detector" in out
    assert "ok:" not in out


def test_run_lifecycle_pending_executing_finalize(tmp_path):
    for criterion in ("a", "b", "c"):
        run(tmp_path, "add", "-i", criterion, "-r", "r", "-d", "d")
    run(tmp_path, "pending", "a", "b", "c", "--run-id", "R1")
    run(tmp_path, "executing", "a", "--run-id", "R1")
    assert run(tmp_path, "executing", "b", "--run-id", "R2", check=False).returncode == 1
    run(tmp_path, "update", "c", "-s", "PASS", "--run-id", "R1")
    run(tmp_path, "finalize", "--run-id", "R1")
    assert row(tmp_path, "a")["status"] == "INCOMPLETE"
    assert row(tmp_path, "b")["status"] == "NOT RUN"
    assert row(tmp_path, "c")["status"] == "PASS"


def test_move_reorders(tmp_path):
    for criterion in ("a", "b", "c"):
        run(tmp_path, "add", "-i", criterion, "-r", "r", "-d", "d")
    run(tmp_path, "move", "c", "--before", "a")
    assert [r["id"] for r in board(tmp_path)["criteria"]] == ["c", "a", "b"]
    run(tmp_path, "move", "c", "--after", "b")
    assert [r["id"] for r in board(tmp_path)["criteria"]] == ["a", "b", "c"]


def test_remove_warns_when_green_and_prints_changelog(tmp_path):
    run(tmp_path, "add", "-i", "c", "-r", "it works", "-d", "d")
    run(tmp_path, "update", "c", "-s", "PASS")
    result = run(tmp_path, "remove", "c", "--why", "superseded")
    assert "lowers the bar" in result.stderr
    assert result.stdout.strip() == "- **Criterion `c` removed** (it works) — superseded"
    assert board(tmp_path)["criteria"] == []


def test_import_detects_axis_and_keeps_unknown_fields(tmp_path):
    source = tmp_path / "old.json"
    source.write_text(json.dumps({"_per_class": "note", "criteria": [
        {"id": "k", "requirement": "r", "detector": "d", "status": "NOT STARTED",
         "class_dependent": True, "per_class": {}, "invariant": "i"}]}))
    project = tmp_path / "proj"
    project.mkdir()
    result = run(project, "import", str(source))
    assert "axis set to 'class'" in result.stderr
    data = board(project)
    assert data["axis"] == "class" and data["_per_class"] == "note"
    assert data["criteria"][0]["class_dependent"] is True
    run(project, "axis", "class", "warrior")
    run(project, "update", "k", "-s", "PASS", "--class", "warrior", "-w", "1h")
    assert row(project, "k")["status"] == "PASS"
    assert row(project, "k")["invariant"] == "i"


def test_import_plain_board_is_verbatim(tmp_path):
    source = tmp_path / "old.json"
    source.write_text('{"criteria": [{"id": "x", "status": "PASS"}]}')
    project = tmp_path / "proj"
    project.mkdir()
    run(project, "import", str(source))
    assert (project / ".ccproject" / "criteria.json").read_bytes() == source.read_bytes()
    assert run(project, "import", str(source), check=False).returncode == 1
