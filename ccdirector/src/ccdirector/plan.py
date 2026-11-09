"""The director's work plan: `.ccdirector/plan.json` and its rendering, plan.md.

The definition of done and the known work live in the project's own criteria
board and defect queue. This file holds only what is the director's: the goal,
and the work items it derived from them.

The director never writes this file with Edit/Write (it is denied every edit
under the project root). It submits JSON to `ccdirector plan submit`, which
validates it here and writes both files. A plan that fails validation is
refused with every problem listed, so the director can fix it in one pass.

Items are not assigned to workers in the plan. Workers take the next ready item
while the work runs (`work.py`); the plan says what the items are, what each
depends on, what it needs exclusive use of, and the command that decides it is
done.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SIZES = ("S", "M", "L")
DIRNAME = Path(".ccdirector")

#: Set by `plan submit`, never by the director: the goal `ccdirector start` was
#: running when this plan was accepted. The plan's own `goal` is the director's
#: wording of it, so it cannot be compared with what the user asked for.
WRITTEN_FOR = "written_for"

#: A worker runs an item's check inside one Bash tool call, which Claude Code caps
#: at 10 minutes; the check must finish inside that with room to report.
MAX_CHECK_SECONDS = 540
DEFAULT_CHECK_SECONDS = 300


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
        timeout = item.get("check_timeout", DEFAULT_CHECK_SECONDS)
        if (not isinstance(timeout, int) or isinstance(timeout, bool)
                or not 1 <= timeout <= MAX_CHECK_SECONDS):
            found.append(f"{where}.check_timeout must be an integer from 1 to "
                         f"{MAX_CHECK_SECONDS} (seconds)")
        priority = item.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            found.append(f"{where}.priority must be an integer >= 1 (1 is most urgent)")
        if item.get("size") not in SIZES:
            found.append(f"{where}.size must be one of {', '.join(SIZES)}")
        for crit in text_list(item.get("serves"), f"{where}.serves"):
            if criteria and crit not in criteria:
                found.append(f"{where}.serves names {crit!r}, which is not in the plan's criteria")
        text_list(item.get("paths"), f"{where}.paths")
        text_list(item.get("resources", []), f"{where}.resources")
        text_list(item.get("depends_on", []), f"{where}.depends_on")
        if "section" in item:
            text(item.get("section"), f"{where}.section")
        if not isinstance(item.get("interface", False), bool):
            found.append(f"{where}.interface must be true or false")

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
    ordered = sorted(plan["items"], key=lambda i: (i["priority"], i["id"]))
    lines += ["## Work items", "",
              "| Pri | Id | Section | What | Serves | Size | Depends on |",
              "|---|---|---|---|---|---|---|"]
    for item in ordered:
        lines.append("| {} | `{}`{} | {} | {} | {} | {} | {} |".format(
            item["priority"], item["id"], " (interface)" if item.get("interface") else "",
            item.get("section") or "—", item["what"].replace("|", "\\|"),
            ", ".join(item["serves"]), item["size"],
            ", ".join(item.get("depends_on") or []) or "—"))
    lines += [""]
    for item in ordered:
        # A plan accepted before items had checks still renders, so the director
        # can read it and resubmit it with checks.
        check = (f"`{item['check']}` (within "
                 f"{item.get('check_timeout', DEFAULT_CHECK_SECONDS)} s)"
                 if item.get("check") else "none (plan predates checks; resubmit with one)")
        lines += [f"### `{item['id']}`", "", item["what"], "",
                  "- Paths: " + (", ".join(f"`{p}`" for p in item.get("paths") or []) or "—"),
                  f"- Check: {check}"]
        if item.get("resources"):
            lines.append("- Resources: " + ", ".join(item["resources"]))
        if item.get("interface"):
            lines.append("- Interface change: runs while no other item is in progress")
        lines.append("")
    if plan.get("risks"):
        lines += ["## Risks and unknowns", ""] + [f"- {r}" for r in plan["risks"]] + [""]
    return "\n".join(lines)


SCHEMA_TEXT = f"""{{
  "goal": "one paragraph",
  "source": "directive | board | queue | scan",
  "gate": "the command that decides done, e.g. ccproject_criteria.py --gate",
  "criteria": ["board criterion ids that make up the goal"],
  "items": [
    {{"id": "short-id",
     "what": "the work, specific enough for a worker starting cold",
     "serves": ["criterion ids"],
     "priority": 1,
     "section": "optional: the area of the project (dlm, journal, ...); a worker
                 keeps to one section where it can",
     "depends_on": ["item ids that must be done first"],
     "paths": ["files and globs it is expected to edit (guidance, not a lock)"],
     "resources": ["test machines, devices, rigs it needs exclusive use of"],
     "interface": false,
     "check": "shell command, run from the project root, exit 0 = the item is done",
     "check_timeout": {DEFAULT_CHECK_SECONDS},
     "size": "S | M | L"}}
  ],
  "risks": ["what could not be determined, and what is needed from the user"]
}}

- check_timeout: seconds, 1 to {MAX_CHECK_SECONDS}; default {DEFAULT_CHECK_SECONDS}.
- interface: true for an item that changes something many items use (a shared
  struct, a signature in a shared header, a locking rule). It is handed out only
  when no other item is in progress, and nothing else is handed out until it is
  done.
- Two items naming the same resource never run at the same time."""
