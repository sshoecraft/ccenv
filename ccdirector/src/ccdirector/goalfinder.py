"""Determine a project's goal when the user did not give one.

One read-only `claude -p` session (see adapter.py) with its own system prompt
(prompts/goalfinder.md). ccdirector first reads the project's criteria board
and defect queue through their tools and hands them to it, so the session
needs only Read, Grep and Glob.

The answer is checked twice: the CLI validates it against SCHEMA, then
`validate` re-checks every type and cross-checks it against the project's
records. An answer that fails either is an error, never a goal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import adapter, ccproject

PROMPT_FILE = Path(__file__).parent / "prompts" / "goalfinder.md"
TOOLS = ["Read", "Grep", "Glob"]
DEFAULT_TIMEOUT = 900.0
SOURCES = ("board", "queue", "scan", "none")
CRITERION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

PROPOSED = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "requirement": {"type": "string"},
        "detector": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["id", "requirement", "detector", "why"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "goal": {"type": "string"},
        "source": {"type": "string", "enum": list(SOURCES)},
        "criteria": {
            "type": "object",
            "properties": {
                "existing": {"type": "array", "items": {"type": "string"}},
                "proposed": {"type": "array", "items": PROPOSED},
            },
            "required": ["existing", "proposed"],
            "additionalProperties": False,
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["found", "goal", "source", "criteria", "evidence", "reason"],
    "additionalProperties": False,
}


class GoalError(RuntimeError):
    """The goal-finder could not run, or its answer was not usable."""


@dataclass
class Goal:
    found: bool
    goal: str
    source: str
    existing: list = field(default_factory=list)
    proposed: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    reason: str = ""
    cost_usd: float = 0.0
    model: str = ""

    def as_dict(self) -> dict:
        return {"found": self.found, "goal": self.goal, "source": self.source,
                "criteria": {"existing": self.existing, "proposed": self.proposed},
                "evidence": self.evidence, "reason": self.reason,
                "cost_usd": self.cost_usd, "model": self.model}


def strings(value: object, where: str) -> list:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GoalError(f"{where} must be a list of strings, got {value!r:.200}")
    return value


def validate(data: object, shot: ccproject.Snapshot) -> Goal:
    """Re-check the structured answer field by field. The CLI's schema check is
    not trusted alone: a string "false" read as a bool once executed a trade a
    reviewer had rejected (warrior)."""
    if not isinstance(data, dict):
        raise GoalError(f"answer is not an object: {data!r:.200}")
    found = data.get("found")
    if not isinstance(found, bool):
        raise GoalError(f"found must be true or false, got {found!r}")
    goal, source, reason = data.get("goal"), data.get("source"), data.get("reason")
    for name, value in (("goal", goal), ("source", source), ("reason", reason)):
        if not isinstance(value, str):
            raise GoalError(f"{name} must be a string, got {value!r:.200}")
    if source not in SOURCES:
        raise GoalError(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
    criteria = data.get("criteria")
    if not isinstance(criteria, dict):
        raise GoalError(f"criteria must be an object, got {criteria!r:.200}")
    existing = strings(criteria.get("existing"), "criteria.existing")
    proposed = criteria.get("proposed")
    if not isinstance(proposed, list):
        raise GoalError(f"criteria.proposed must be a list, got {proposed!r:.200}")
    evidence = strings(data.get("evidence"), "evidence")

    if not found:
        if source != "none":
            raise GoalError(f"not found, but source is {source!r} (expected 'none')")
        if not reason.strip():
            raise GoalError("not found, and no reason given")
        return Goal(found=False, goal="", source="none", evidence=evidence, reason=reason)

    if source == "none":
        raise GoalError("found, but source is 'none'")
    if not goal.strip():
        raise GoalError("found, but the goal is empty")
    if source == "board" and not shot.has_board:
        raise GoalError("source is 'board', but this project has no criteria board")
    if source == "queue" and not shot.has_queue:
        raise GoalError("source is 'queue', but this project has no defect queue")
    unknown = [i for i in existing if i not in shot.board_ids]
    if unknown:
        raise GoalError(f"criteria.existing names ids not on the board: {', '.join(unknown)}")

    seen = set(shot.board_ids)
    checked = []
    for index, item in enumerate(proposed):
        where = f"criteria.proposed[{index}]"
        if not isinstance(item, dict):
            raise GoalError(f"{where} is not an object")
        for key in ("id", "requirement", "detector", "why"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise GoalError(f"{where}.{key} must be a non-empty string")
        if not CRITERION_ID.match(item["id"]):
            raise GoalError(f"{where}.id {item['id']!r} is not a usable criterion id")
        if item["id"] in seen:
            raise GoalError(f"{where}.id {item['id']!r} is already taken")
        seen.add(item["id"])
        checked.append({k: item[k] for k in ("id", "requirement", "detector", "why")})
    if source == "scan" and not checked:
        raise GoalError("source is 'scan' but no criteria were proposed: "
                        "nothing would define done")
    return Goal(found=True, goal=goal, source=source, existing=existing,
                proposed=checked, evidence=evidence, reason=reason)


def build_prompt(root: Path, shot: ccproject.Snapshot) -> str:
    parts = [f"Project directory: {root}", ""]
    if shot.has_board:
        parts += ["## The project's criteria board (ccproject_criteria.py -v)", "",
                  shot.board_text or "(empty board)", ""]
    else:
        parts += ["## Criteria board", "", "This project has no criteria board.", ""]
    if shot.has_queue:
        parts += ["## The project's defect queue (ccproject_defects.py -d)", "",
                  shot.queue_text or "(empty queue)", ""]
    else:
        parts += ["## Defect queue", "", "This project has no defect queue.", ""]
    parts += ["Determine this project's goal as your instructions describe, "
              "and answer with the JSON object."]
    return "\n".join(parts)


def find(root: Path, timeout: float = DEFAULT_TIMEOUT, model: str | None = None) -> Goal:
    try:
        shot = ccproject.snapshot(root)
    except ccproject.ToolError as exc:
        raise GoalError(str(exc)) from exc
    try:
        reply = adapter.request(PROMPT_FILE, build_prompt(root, shot), SCHEMA, root,
                                TOOLS, timeout, model=model)
    except adapter.AdapterError as exc:
        raise GoalError(str(exc)) from exc
    goal = validate(reply.data, shot)
    goal.cost_usd, goal.model = reply.cost_usd, reply.model
    return goal
