"""The director's work plan: `.ccdirector/plan.json` and its rendering, plan.md.

The definition of done and the known work live in the project's own criteria
board and defect queue. This file holds only what is the director's: the goal,
and the work items it derived from them.

The director never writes this file with Edit/Write (it is denied every edit
under the project root). It submits JSON to `ccdirector plan submit`, which
validates it here and writes both files. A plan that fails validation is
refused with every problem listed, so the director can fix it in one pass.

Items are not assigned to workers in the plan. Workers take the next ready item
while the work runs (`work.py`) and each keeps its item until it is done; the
plan says what the items are, what each depends on, and the command that
decides it is done.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

SIZES = ("S", "M", "L")
DIRNAME = Path(".ccdirector")

#: A resource id: short, so every item and every worker names a resource the same
#: way. Prose descriptions made each item name the rig differently, and resources
#: compared as text then never matched, so nothing ever waited for the rig.
RESOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")

#: Set by `plan submit`, never by the director: the goal `ccdirector start` was
#: running when this plan was accepted. The plan's own `goal` is the director's
#: wording of it, so it cannot be compared with what the user asked for.
WRITTEN_FOR = "written_for"

#: An item is a whole goal, and its check proves the goal, which can take a long
#: test. Workers run checks through their blocking `item_done` tool, so there is no
#: limit unless the item sets `check_timeout` (seconds; 0 or absent = none). A cap
#: that fit inside one Bash call (540 s) made directors write checks that proved
#: only a step, and items shrank to steps.
NO_ITEM_RESOURCES = (
    "an item no longer holds a resource for its whole run, because an item lasts as long "
    "as its goal and the hold made every other item naming it wait; remove the item's "
    "`resources` and let its worker `acquire` units for each run")
NO_INTERFACE = (
    "interface items are gone, because one stopped every other worker for as long as it "
    "lasted; make the shared change an item of its own and list it in the `depends_on` "
    "of the items that need it")


def folder(root: Path) -> Path:
    return root / DIRNAME


def problems(plan: object) -> list[str]:
    """Everything wrong with a submitted plan; an empty list means it is valid."""
    if not isinstance(plan, dict):
        return ["the plan must be a JSON object"]
    found = []

    def text(value, where, empty_ok=False):
        if not isinstance(value, str) or (not empty_ok and not value.strip()):
            found.append(f"{where} must be a non-empty string")
            return False
        return True

    def text_list(value, where):
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            found.append(f"{where} must be a list of non-empty strings")
            return []
        return value

    text(plan.get("goal"), "goal")
    text(plan.get("source"), "source")
    text(plan.get("gate"), "gate", empty_ok=True)
    criteria = set(text_list(plan.get("criteria"), "criteria"))
    text_list(plan.get("risks", []), "risks")
    registry = plan.get("resources", {})
    if not isinstance(registry, dict):
        found.append("resources must be an object: {id: {capacity, what}}")
        registry = {}
    for rid, spec in registry.items():
        where = f"resources[{rid!r}]"
        if not RESOURCE_ID.match(rid):
            found.append(f"{where}: an id is 1-40 characters of a-z, 0-9, '.', '_', '-', "
                         "starting with a letter or digit (e.g. 'rig', 'vm-pool')")
        if not isinstance(spec, dict):
            found.append(f"{where} must be an object with capacity and what")
            continue
        capacity = spec.get("capacity")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            found.append(f"{where}.capacity must be an integer >= 1 (units that can be in "
                         "use at once: 1 for a rig only one run may use, 32 for a pool of 32 "
                         "nodes)")
        text(spec.get("what"), f"{where}.what")

    items = plan.get("items")
    ids = []
    if not isinstance(items, list) or not items:
        found.append("items must be a non-empty list")
        items = []
    for n, item in enumerate(items):
        where = f"items[{n}]"
        if not isinstance(item, dict):
            found.append(f"{where} must be an object")
            continue
        if text(item.get("id"), f"{where}.id"):
            if item["id"] in ids:
                found.append(f"{where}.id {item['id']!r} is used twice")
            ids.append(item["id"])
        text(item.get("what"), f"{where}.what")
        text(item.get("check"), f"{where}.check")
        timeout = item.get("check_timeout", 0)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
            found.append(f"{where}.check_timeout must be an integer of seconds, 0 or more "
                         "(0 or absent: no limit)")
        priority = item.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            found.append(f"{where}.priority must be an integer >= 1 (1 is most urgent)")
        if item.get("size") not in SIZES:
            found.append(f"{where}.size must be one of {', '.join(SIZES)}")
        for crit in text_list(item.get("serves"), f"{where}.serves"):
            if criteria and crit not in criteria:
                found.append(f"{where}.serves names {crit!r}, which is not in the plan's criteria")
        text_list(item.get("paths"), f"{where}.paths")
        if item.get("resources"):
            found.append(f"{where}.resources: {NO_ITEM_RESOURCES}")
        text_list(item.get("depends_on", []), f"{where}.depends_on")
        if "section" in item:
            text(item.get("section"), f"{where}.section")
        if item.get("interface"):
            found.append(f"{where}.interface: {NO_INTERFACE}")

    known = set(ids)
    for n, item in enumerate(items):
        if isinstance(item, dict):
            for dep in item.get("depends_on", []) or []:
                if isinstance(dep, str) and dep not in known:
                    found.append(f"items[{n}].depends_on names unknown item {dep!r}")
    found += cycles(items)
    return found


def cycles(items: list) -> list[str]:
    graph = {i["id"]: [d for d in (i.get("depends_on") or []) if isinstance(d, str)]
             for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)}
    state = {}
    found = []

    def visit(node, trail):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            found.append("dependency cycle: " + " -> ".join(trail + [node]))
            return
        state[node] = "open"
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, trail + [node])
        state[node] = "done"

    for node in graph:
        visit(node, [])
    return found


def write(root: Path, plan: dict) -> tuple[Path, Path]:
    """Write plan.json and plan.md atomically (temp file in the same folder, then rename)."""
    where = folder(root)
    where.mkdir(parents=True, exist_ok=True)
    json_path, md_path = where / "plan.json", where / "plan.md"
    atomic(json_path, json.dumps(plan, indent=2) + "\n")
    atomic(md_path, render(plan))
    return json_path, md_path


def atomic(path: Path, body: str) -> None:
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def set_aside(root: Path) -> None:
    """Move the plan and its progress record out of the way for a replan, so nothing
    can mistake them for the new one's. They stay readable as .backup files; the next
    replan overwrites those."""
    for name in ("plan.json", "plan.md", "progress.json"):
        path = folder(root) / name
        if path.exists():
            path.replace(path.with_name(name + ".backup"))


def load(root: Path) -> dict | None:
    path = folder(root) / "plan.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def item_map(plan: dict | None) -> dict:
    return {i["id"]: i for i in (plan or {}).get("items", []) if isinstance(i, dict)}


def render(plan: dict) -> str:
    lines = ["# Work plan", "", "## Goal", "", plan["goal"], "",
             f"Source: {plan['source']}", ""]
    if plan.get("gate"):
        lines += [f"Done when `{plan['gate']}` exits 0.", ""]
    if plan.get("criteria"):
        lines += ["Criteria: " + ", ".join(f"`{c}`" for c in plan["criteria"]), ""]
    if plan.get("resources"):
        lines += ["## Shared resources", "", "| Id | Capacity | What |", "|---|---|---|"]
        lines += [f"| `{rid}` | {spec.get('capacity')} | "
                  f"{str(spec.get('what', '')).replace('|', chr(92) + '|')} |"
                  for rid, spec in sorted(plan["resources"].items())]
        lines += [""]
    ordered = sorted(plan["items"], key=lambda i: (i["priority"], i["id"]))
    lines += ["## Work items", "",
              "| Pri | Id | Section | What | Serves | Size | Depends on |",
              "|---|---|---|---|---|---|---|"]
    for item in ordered:
        lines.append("| {} | `{}` | {} | {} | {} | {} | {} |".format(
            item["priority"], item["id"],
            item.get("section") or "—", item["what"].replace("|", "\\|"),
            ", ".join(item["serves"]), item["size"],
            ", ".join(item.get("depends_on") or []) or "—"))
    lines += [""]
    for item in ordered:
        # A plan accepted before items had checks still renders, so the director
        # can read it and resubmit it with checks.
        limit = item.get("check_timeout")
        check = ((f"`{item['check']}`" + (f" (within {limit} s)" if limit else " (no time limit)"))
                 if item.get("check") else "none (plan predates checks; resubmit with one)")
        lines += [f"### `{item['id']}`", "", item["what"], "",
                  "- Paths: " + (", ".join(f"`{p}`" for p in item.get("paths") or []) or "—"),
                  f"- Check: {check}", ""]
    if plan.get("risks"):
        lines += ["## Risks and unknowns", ""] + [f"- {r}" for r in plan["risks"]] + [""]
    return "\n".join(lines)


SCHEMA_TEXT = f"""{{
  "goal": "one paragraph",
  "source": "directive | board | queue | scan",
  "gate": "the command that decides done, e.g. ccproject_criteria.py --gate",
  "criteria": ["board criterion ids that make up the goal"],
  "resources": {{
    "rig": {{"capacity": 1, "what": "the test rig; one run at a time"}},
    "vm-pool": {{"capacity": 32, "what": "32 VM nodes; a 2-node lap takes 2"}}
  }},
  "items": [
    {{"id": "short-id",
     "what": "the goal, specific enough for a worker starting cold to carry it to done",
     "serves": ["criterion ids"],
     "priority": 1,
     "section": "optional: the area of the project (dlm, journal, ...); a worker
                 keeps to one section where it can",
     "depends_on": ["item ids that must be done first"],
     "paths": ["files and globs it is expected to edit (guidance, not a lock)"],
     "check": "shell command, run from the project root, exit 0 = the goal is met",
     "check_timeout": 0,
     "size": "S | M | L"}}
  ],
  "risks": ["what could not be determined, and what is needed from the user"]
}}

- An item is a goal one worker owns from start to finish. It keeps it until the
  check passes; a failed check never takes it away.
- check: proves the goal itself, however long that takes (a long test is fine).
- check_timeout: seconds, optional; 0 or absent means no limit.
- depends_on: also how a shared change (a struct, a signature, a locking rule)
  goes first: make it an item of its own and list it here.
- resources: every machine, rig, device or pool that workers share, declared
  once by a short id with its capacity (units usable at once). Workers take
  units only for each run (`acquire` in their tools) and wait, at no cost,
  while others hold them. Items do not name resources."""
