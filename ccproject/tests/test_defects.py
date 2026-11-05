"""ccproject_defects.py, driven as a subprocess in a throwaway project directory."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ccproject_defects.py"


def run(cwd, *argv, check=True):
    result = subprocess.run([sys.executable, str(SCRIPT), *argv], cwd=cwd,
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"exit {result.returncode}: {result.stderr}")
    return result


def ledger(project):
    return json.loads((project / ".ccproject" / "defects.json").read_text())


def test_empty_queue_lists_and_writes_nothing(tmp_path):
    out = run(tmp_path).stdout
    assert "no open defects." in out
    assert not (tmp_path / ".ccproject").exists()


def test_add_mints_id_from_summary_and_suffixes_collisions(tmp_path):
    run(tmp_path, "add", "-m", "Mount hangs after failover", "-s", "high")
    run(tmp_path, "add", "-m", "Mount hangs after failover")
    ids = [d["id"] for d in ledger(tmp_path)["defects"]]
    assert ids == ["D-MOUNT-HANGS-AFTER-FAILOVER", "D-MOUNT-HANGS-AFTER-FAILOVER-2"]
    first = ledger(tmp_path)["defects"][0]
    assert first["severity"] == "high" and first["opened"]


def test_overlong_id_is_refused_not_truncated(tmp_path):
    result = run(tmp_path, "add", "-m", "x", "--id", "D-" + "A" * 90, check=False)
    assert result.returncode == 1
    assert "limit is 80" in result.stderr


def test_show_by_unique_substring_and_ambiguity(tmp_path):
    run(tmp_path, "add", "-m", "alpha one", "-w", "seen twice")
    run(tmp_path, "add", "-m", "alpha two")
    assert "seen twice" in run(tmp_path, "show", "ONE").stdout
    ambiguous = run(tmp_path, "show", "alpha", check=False)
    assert ambiguous.returncode == 1 and "matches 2 entries" in ambiguous.stderr


def test_replace_refused_without_flag_and_append_separates(tmp_path):
    run(tmp_path, "add", "-m", "thing", "-w", "first")
    refused = run(tmp_path, "update", "thing", "-w", "second", check=False)
    assert refused.returncode == 1 and "--replace" in refused.stderr
    run(tmp_path, "update", "thing", "-W", "second")
    assert ledger(tmp_path)["defects"][0]["evidence"] == "first ===== second"
    run(tmp_path, "update", "thing", "-w", "third", "--replace")
    entry = ledger(tmp_path)["defects"][0]
    assert entry["evidence"] == "third" and entry["updated"]


def test_append_to_list_valued_field_adds_an_item(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "defects.json").write_text(json.dumps(
        {"defects": [{"id": "D-OLD", "severity": "major", "summary": "s", "next": ["a", "b"]}]}))
    run(tmp_path, "update", "D-OLD", "-a", "c")
    assert ledger(tmp_path)["defects"][0]["next"] == ["a", "b", "c"]


def test_update_with_nothing_is_an_error(tmp_path):
    run(tmp_path, "add", "-m", "thing")
    assert run(tmp_path, "update", "thing", check=False).returncode == 1


def test_remove_prints_changelog_line_and_drops_record(tmp_path):
    run(tmp_path, "add", "-m", "Leaks a lock", "-s", "critical")
    out = run(tmp_path, "remove", "LEAKS", "--fixed", "--why", "lock released on every path").stdout
    assert out.strip() == "- **Leaks a lock** — fixed: lock released on every path"
    assert ledger(tmp_path)["defects"] == []
    assert run(tmp_path, "remove", "x", check=False).returncode == 2  # --why required


def test_rename_refuses_existing_id(tmp_path):
    run(tmp_path, "add", "-m", "one")
    run(tmp_path, "add", "-m", "two")
    assert run(tmp_path, "rename", "D-ONE", "D-TWO", check=False).returncode == 1
    run(tmp_path, "rename", "D-ONE", "D-FIRST")
    assert {d["id"] for d in ledger(tmp_path)["defects"]} == {"D-FIRST", "D-TWO"}


def test_list_is_in_severity_order(tmp_path):
    run(tmp_path, "add", "-m", "minor thing", "-s", "minor")
    run(tmp_path, "add", "-m", "critical thing", "-s", "critical")
    run(tmp_path, "add", "-m", "high thing", "-s", "high")
    lines = [l.split()[0] for l in run(tmp_path).stdout.splitlines() if l.strip()][:3]
    assert lines == ["critical", "high", "minor"]


def test_gate_exit_and_exclusion_with_missing_field_still_blocking(tmp_path):
    assert run(tmp_path, "--gate").returncode == 0
    run(tmp_path, "add", "-m", "harness only", "--id", "D-H")
    run(tmp_path, "add", "-m", "unclassified", "--id", "D-U")
    store = tmp_path / ".ccproject" / "defects.json"
    data = json.loads(store.read_text())
    data["defects"][0]["scope"] = "harness"
    store.write_text(json.dumps(data))
    run(tmp_path, "gate-exclude", "scope", "harness")
    gated = run(tmp_path, "--gate", "--json", check=False)
    assert gated.returncode == 1
    assert [d["id"] for d in json.loads(gated.stdout)["defects"]] == ["D-U"]
    run(tmp_path, "remove", "D-U", "--why", "disproved by trace", "--disproved")
    assert run(tmp_path, "--gate").returncode == 0
    run(tmp_path, "gate-exclude", "scope")
    assert "gate_exclude" not in ledger(tmp_path)
    assert run(tmp_path, "--gate", check=False).returncode == 1


def test_gate_require_counts_only_rated_blockers_and_notes_the_unrated(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "defects.json").write_text(json.dumps({"defects": [
        {"id": "D-CORRUPT", "severity": "critical", "summary": "s", "impact": "integrity"},
        {"id": "D-SLOW", "severity": "major", "summary": "s", "impact": "noblock"},
        {"id": "D-RIG", "severity": "high", "summary": "s", "impact": "stability",
         "scope": "harness"},
        {"id": "D-UNRATED", "severity": "high", "summary": "s"}]}))
    run(tmp_path, "gate-require", "impact", "integrity", "stability")
    run(tmp_path, "gate-exclude", "scope", "harness")
    gated = run(tmp_path, "--gate", "--json", check=False)
    assert gated.returncode == 1
    assert [d["id"] for d in json.loads(gated.stdout)["defects"]] == ["D-CORRUPT"]
    assert "NOTE: 1 more record(s)" in run(tmp_path, "--gate", check=False).stdout
    run(tmp_path, "gate-require", "impact")
    assert "gate_require" not in ledger(tmp_path)


def test_unknown_fields_survive_a_write(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "defects.json").write_text(json.dumps(
        {"_comment": "keep me", "defects": [
            {"id": "D-X", "severity": "high", "summary": "s", "impact": "integrity",
             "cells": [{"config": "2/a", "row": "r", "iso": "2026-01-01T00:00:00Z"}]}]}))
    run(tmp_path, "update", "D-X", "-W", "more")
    data = ledger(tmp_path)
    assert data["_comment"] == "keep me"
    assert data["defects"][0]["impact"] == "integrity"
    assert data["defects"][0]["cells"][0]["row"] == "r"
    assert "impact" in run(tmp_path, "show", "D-X").stdout


def test_subdirectory_uses_the_project_store(tmp_path):
    run(tmp_path, "add", "-m", "top level")
    deep = tmp_path / "src" / "deep"
    deep.mkdir(parents=True)
    run(deep, "add", "-m", "from below")
    assert not (deep / ".ccproject").exists()
    assert len(ledger(tmp_path)["defects"]) == 2


def test_import_copies_verbatim_and_refuses_overwrite(tmp_path):
    source = tmp_path / "old.json"
    source.write_text('{"defects": [{"id": "D-A", "severity": "minor", "summary": "\\u2014"}]}')
    project = tmp_path / "proj"
    project.mkdir()
    run(project, "import", str(source))
    assert (project / ".ccproject" / "defects.json").read_bytes() == source.read_bytes()
    assert run(project, "import", str(source), check=False).returncode == 1


def test_criteria_links_add_and_drop(tmp_path):
    run(tmp_path, "add", "-m", "thing", "--criterion", "c1")
    run(tmp_path, "update", "thing", "--criterion", "c2", "--drop-criterion", "c1")
    assert ledger(tmp_path)["defects"][0]["criteria"] == ["c2"]
    run(tmp_path, "update", "thing", "--drop-criterion", "c2")
    assert "criteria" not in ledger(tmp_path)["defects"][0]


def test_project_key_survives_every_write(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "defects.json").write_text(json.dumps(
        {"project": {"queue": 1}, "defects": [
            {"id": "D-X", "severity": "high", "summary": "s", "project": {"record": 2}}]}))
    run(tmp_path, "update", "D-X", "-W", "more", "-s", "critical")
    run(tmp_path, "rename", "D-X", "D-Y")
    run(tmp_path, "gate-exclude", "scope", "harness")
    data = ledger(tmp_path)
    assert data["project"] == {"queue": 1}
    assert data["defects"][0]["project"] == {"record": 2}


DRIVER = '''
API = 1


def options(parser):
    parser.add_argument("--at-config")


def blocks(data, entry, args):
    at = getattr(args, "at_config", None)
    if at is None:
        return None
    return at in entry.get("project", {}).get("blocks", [])


def stats(data, args):
    print("open:", len(data["defects"]))
    ccproject.save(data)
    return 0


def commands(sub):
    sub.add_parser("stats").set_defaults(driver_command=stats)
'''


def test_driver_blocks_hook_option_and_command(tmp_path):
    store = tmp_path / ".ccproject"
    store.mkdir()
    (store / "driver.py").write_text(DRIVER)
    (store / "defects.json").write_text(json.dumps({"defects": [
        {"id": "D-TWO", "severity": "high", "summary": "s", "project": {"blocks": ["2"]}},
        {"id": "D-ANY", "severity": "minor", "summary": "s"}]}))
    gated = json.loads(run(tmp_path, "--gate", "--json", "--at-config", "2", check=False).stdout)
    assert [d["id"] for d in gated["defects"]] == ["D-TWO"]
    assert run(tmp_path, "--gate", "--at-config", "1").returncode == 0
    assert run(tmp_path, "--gate", check=False).returncode == 1  # no option: generic, all block
    assert "open: 2" in run(tmp_path, "stats").stdout


def test_store_ignores_its_lock_and_temp_files(tmp_path):
    run(tmp_path, "add", "-m", "thing")
    assert (tmp_path / ".ccproject" / ".gitignore").read_text() == ".lock\n*.tmp\n"


def test_concurrent_writers_lose_nothing(tmp_path):
    run(tmp_path, "add", "-m", "seed")
    procs = [subprocess.Popen([sys.executable, str(SCRIPT), "add", "-m", f"parallel {n}"],
                              cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for n in range(12)]
    assert all(p.wait() == 0 for p in procs)
    assert len(ledger(tmp_path)["defects"]) == 13
