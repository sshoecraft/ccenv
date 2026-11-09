"""Where a run stands, in one block at the top of `ccdirector wait` and `status`.

The director's screen is the only one the user watches and workers run headless,
so this block is how the user tells a run that is working from one that is only
busy. Finishing items is not progress on its own: an item's check proves a piece of
work happened, while the board says whether the result is good enough. The block
therefore sets items done against criteria green, and counts how often the gate
has refused since a criterion was last gained.

`.ccdirector/progress.json` holds what the board and the event log cannot say
later: the most criteria ever green, when that was reached, and the refusals.
"""
from __future__ import annotations

import datetime
import json
import re
import time
from pathlib import Path

from . import ccproject, plan as planmod, work

FILE = "progress.json"
#: Refusals with no criterion gained before the block calls it likely looping. One
#: refusal is normal: it is how the director learns what the plan missed.
WARN_AFTER_REFUSALS = 2
#: Second opinions with no criterion gained before the block calls the run stuck.
WARN_AFTER_CONSULTS = 2
#: Rounds with nothing gained before the run ends itself; 0 means never. A run exists
#: to keep trying until the gate passes, so a limit is only for someone who asks for one.
DEFAULT_ROUND_LIMIT = 0
#: Replans between required consults. Advisor calls are expensive.
CONSULT_EVERY = 3
#: A backgrounded consult with no answer after this long is treated as lost, because
#: the task does not survive its session and nothing else would ever clear it.
CONSULT_PATIENCE_SECONDS = 1800
TASK_STATUS = re.compile(r"<status>(\w+)</status>")
TAIL_BYTES = 16_384
SAID_WIDTH = 110
#: A worker's log mixes its sentences with tool lines (`→ Bash`, `  ok 5 lines`) and
#: raw command output. A sentence starts with a capital and ends with punctuation.
SENTENCE = re.compile(r"^[A-Z].* .*[.:)]$")


def load(root: Path) -> dict:
    where = work.path(root, FILE)
    if not where.is_file():
        return {}
    try:
        return json.loads(where.read_text())
    except ValueError:
        return {}


def save(root: Path, record: dict) -> None:
    planmod.atomic(work.path(root, FILE), json.dumps(record, indent=2) + "\n")


def board(root: Path) -> tuple[int, int] | None:
    """(green, total) from the board's own verdict, or None when it cannot say."""
    try:
        report = ccproject.read_json(ccproject.CRITERIA_TOOL, root)
    except ccproject.ToolError:
        return None
    verdict = report.get("verdict") if isinstance(report, dict) else None
    if not isinstance(verdict, dict):
        return None
    return int(verdict.get("green", 0)), int(verdict.get("total", 0))


def update(root: Path) -> dict:
    """Record the board's count. A gain is only a new best, so a criterion that
    goes red and green again does not read as progress."""
    counted = board(root)       # outside the lock: the board can take seconds to grade
    with work.locked(root):
        record = load(root)
        if counted:
            green, total = counted
            if green > record.get("best", -1):
                record.update(best=green, gained=work.now(), refusals_since_gain=0,
                              consults_since_gain=0, rounds_since_gain=0)
            record.update(green=green, total=total)
            save(root, record)
    return record


def plan_revisions(root: Path) -> int:
    """Times the plan really changed. A restart re-syncs with no changes and is not one."""
    return sum(1 for e in work.events(root) if e.get("kind") == "plan_accepted" and e.get("changes"))


def record_refusal(root: Path) -> bool:
    """Count a gate refusal. True when it closed a round: every item done, and the plan
    changed since the last round closed. The director repeating `goal-done`, or a
    restart on the same plan, is not another round."""
    before = load(root).get("best")
    after = update(root)        # first, so a criterion just gained resets the counts
    gained = before is not None and after.get("best", -1) > before
    revisions = plan_revisions(root)
    finished = work.all_done(work.load(root), planmod.load(root))
    with work.locked(root):
        record = load(root)
        rounds = record.get("rounds_since_gain", record.get("refusals_since_gain", 0))
        closed = finished and record.get("round_plan") != revisions
        if closed:
            record["round_plan"] = revisions
            rounds += 0 if gained else 1
            replans = record.get("replans_since_consult", 0) + 1
            record["replans_since_consult"] = replans
            if replans >= CONSULT_EVERY:
                record.update(consult_required=True, consults_at_round=record.get("consults", 0))
        record["rounds_since_gain"] = rounds
        record["refusals"] = record.get("refusals", 0) + 1
        record["refusals_since_gain"] = record.get("refusals_since_gain", 0) + 1
        save(root, record)
        work.event(root, "gate_refused", green=record.get("green"), total=record.get("total"))
    return closed


def round_limit(root: Path) -> int:
    """The limit `ccdirector start` was given; a run started before it existed gets the
    default."""
    try:
        goal = json.loads(work.path(root, "goal.json").read_text())
        return int(goal.get("round_limit", DEFAULT_ROUND_LIMIT))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_ROUND_LIMIT


def limit_reached(root: Path) -> bool:
    limit = round_limit(root)
    return limit > 0 and load(root).get("rounds_since_gain", 0) >= limit


def consult_due(root: Path) -> bool:
    """The replan count reached its limit and no advisor has answered since."""
    settle_consults(root)
    record = load(root)
    return bool(record.get("consult_required")) and \
        record.get("consults", 0) <= record.get("consults_at_round", 0)


def consult_waiting(root: Path) -> list[str]:
    """Advisors the director has asked that have not answered yet."""
    settle_consults(root)
    return [e["advisor"] for e in load(root).get("consults_pending", []) if e["by"] == "director"]


def replan_submitted(root: Path) -> None:
    with work.locked(root):
        record = load(root)
        if record.pop("consult_required", None):
            record.pop("consults_at_round", None)
            record["replans_since_consult"] = 0     # reached when the consult was waived
            save(root, record)


def record_backgrounded(root: Path, advisor: str, by: str, task: str, transcript: str) -> None:
    """Claude Code moved a consult to the background: asked, not answered."""
    with work.locked(root):
        record = load(root)
        record.setdefault("consults_pending", []).append(
            {"advisor": advisor, "by": by, "task": task, "transcript": transcript,
             "since": time.time()})
        save(root, record)
        work.event(root, "consult_backgrounded", advisor=advisor, by=by, task=task)


def task_status(entry: dict) -> str | None:
    """The task's final status once its answer has reached the session. Claude Code
    logs a queued notification first and the delivered one later; only delivery counts,
    because until then the session cannot have read the answer."""
    tag = f"<task-id>{entry['task']}</task-id>"
    try:
        with open(entry["transcript"], errors="replace") as handle:
            for line in handle:
                if tag not in line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("type") != "user":
                    continue
                found = TASK_STATUS.search(json.dumps(message.get("message", {}).get("content")))
                if found:
                    return found.group(1)
    except OSError:
        pass
    return None


def settle_consults(root: Path) -> None:
    """Count backgrounded consults whose answer has arrived and forget lost ones."""
    pending = load(root).get("consults_pending")
    if not pending:
        return
    answered, settled = [], set()
    for entry in pending:
        status = task_status(entry)
        if status == "completed":
            answered.append(entry)
        if status or time.time() - entry["since"] >= CONSULT_PATIENCE_SECONDS:
            settled.add(entry["task"])
    if not settled:
        return
    with work.locked(root):
        record = load(root)
        record["consults_pending"] = [e for e in record.get("consults_pending", [])
                                      if e["task"] not in settled]
        save(root, record)
    for entry in answered:
        record_consult(root, entry["advisor"], entry["by"])


def forget_pending(root: Path) -> None:
    """A backgrounded task does not survive its session, so a new run cannot be owed one."""
    with work.locked(root):
        record = load(root)
        if record.pop("consults_pending", None) is not None:
            save(root, record)


def record_consult(root: Path, advisor: str, by: str) -> None:
    """Count an answered consult. The director's are about the plan's direction and a
    worker's are about its item, so a worker's touches only its item's count."""
    if by != "director":
        work.item_consulted(root, by)
    with work.locked(root):
        record = load(root)
        if by == "director":
            record["consults"] = record.get("consults", 0) + 1
            record["consults_since_gain"] = record.get("consults_since_gain", 0) + 1
            record["replans_since_consult"] = 0
        else:
            record["worker_consults"] = record.get("worker_consults", 0) + 1
        save(root, record)
        work.event(root, "consulted", advisor=advisor, by=by)


def elapsed(when: float) -> str:
    minutes = int(max(0, time.time() - when)) // 60
    if minutes < 1:
        return "<1m"
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m"


def worker_line(root: Path, worker: str) -> str:
    """How long since the worker's log moved, and the last thing it said."""
    log = work.path(root, "workers") / f"{worker}.log"
    try:
        info = log.stat()
        with open(log, "rb") as handle:
            handle.seek(max(0, info.st_size - TAIL_BYTES))
            tail = handle.read().decode(errors="replace").splitlines()
    except OSError:
        return "no log yet"
    said = next((line for line in reversed(tail) if SENTENCE.match(line)), "")
    return f"active {elapsed(info.st_mtime)} ago" + (f": {said[:SAID_WIDTH]}" if said else "")


def extensions(root: Path) -> int:
    """Times the plan gained items after the first plan was accepted."""
    grew = sum(1 for e in work.events(root) if e.get("kind") == "plan_accepted"
               and any(str(c).startswith("new item") for c in e.get("changes", [])))
    return max(0, grew - 1)


def criteria_text(record: dict) -> str:
    if not record.get("total"):
        return "criteria unknown"
    text = f"criteria {record['green']}/{record['total']} green"
    if record["green"] < record["best"]:
        text += f" (best {record['best']})"
    gained = datetime.datetime.fromisoformat(record["gained"]).timestamp()
    return f"{text}, no gain for {elapsed(gained)}"


def report(root: Path) -> str:
    record = update(root)
    current = planmod.load(root)
    state = work.load(root)
    tally = work.counts(state, current)
    stale = record.get("refusals_since_gain", 0)
    unhelped = record.get("consults_since_gain", 0)
    limit = round_limit(root)
    rounds = record.get("rounds_since_gain", record.get("refusals_since_gain", 0))
    lines = ["PROGRESS " + " | ".join([
        f"items {tally[work.DONE]}/{sum(tally.values())} done, {tally[work.CLAIMED]} running, "
        f"{tally[work.PARKED]} parked",
        criteria_text(record),
        f"gate refused {record.get('refusals', 0)}x",
        f"plan extended {extensions(root)}x",
        f"consulted {record.get('consults', 0)}x, {unhelped} since gain"
        + (f", workers {record['worker_consults']}x" if record.get("worker_consults") else ""),
        f"rounds without gain {rounds}" + (f"/{limit}" if limit else ""),
        f"replans since consult {record.get('replans_since_consult', 0)}/{CONSULT_EVERY}"])]
    held = sorted((entry["worker"], item_id) for item_id, entry in state["items"].items()
                  if entry["status"] == work.CLAIMED and entry.get("worker"))
    lines += [f"  {worker} on {item_id}: {worker_line(root, worker)}" for worker, item_id in held]
    if stale >= WARN_AFTER_REFUSALS:
        lines.append(f"LIKELY LOOPING: the gate has refused {stale} times with no new "
                     "criterion green; finishing more items is not moving it.")
    if unhelped >= WARN_AFTER_CONSULTS:
        lines.append(f"STUCK: {unhelped} consults with no new criterion green; advice is not "
                     "moving it either. Try something structurally different.")
    waiting = consult_waiting(root)
    if waiting:
        lines.append(f"WAITING ON ADVISOR: {', '.join(waiting)} has not answered; no plan can "
                     "be submitted until it does.")
    elif consult_due(root):
        lines.append(f"CONSULT DUE: {CONSULT_EVERY} replans since the last consult. Consult an "
                     "advisor, wait for its answer, then submit the reworked plan.")
    return "\n".join(lines)
