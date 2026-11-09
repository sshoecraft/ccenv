#!/usr/bin/env python3
"""ccproject_criteria.py -- what the project must achieve, and where each criterion stands.

Each criterion is a requirement, the detector that measures it, and its latest result. A project
may declare one AXIS -- class, config, platform, anything -- and make some criteria a matrix over
it: such a row keeps one cell per axis value under `per_<axis>` and is green only when every
cell is.

Data: `.ccproject/criteria.json` in the project -- the nearest directory at or above the cwd that
already holds a `.ccproject/`, else the cwd itself (created on the first write). The project's
`.ccproject/config.json` may name another file in the repository for it (`"store": {"criteria":
"data/criteria.json"}`), or name the project's own tool for its criteria (`"external":
{"criteria": "tools/criteria.py"}`), in which case this tool refuses to run there. Format:

    {"criteria": [{"id", "requirement", "detector", "why", "phase", "source", "budget_s",
                   "status", "measured", "window", "evidence", "reason", "run_id", "recorded",
                   "history": [...], "values": [...], "per_<axis>": {value: cell}}, ...],
     "axis": "class", "axis_values": [...], "statuses": [...], "green": [...]}

Fields this tool does not know are kept on every save, so a board written by an older or
project-specific tool loads unchanged. The key `"project"` -- on the board, a row or a cell --
is reserved for the project's own data: this tool never reads or rewrites it.

When the project keeps a defect queue, "no blocking defects" is one line of the verdict
(`"defects_gate": false` on the board turns that off).

A project extends the tool with `.ccproject/driver.py` (shared with ccproject_defects.py):
`API = 1`, plus any of `applies(data, row)` -> values, `grade(data, row, value, record, build)`
-> status, `roll_up(data, row, statuses)` -> a matrix row's status from its cells',
`before_result(data, row, value, results, args)`, `on_result(data, row, value,
record, args)`, `blocks(data, entry, args)`, `options(parser)` and `commands(subparsers)`. A hook
answering None means "the generic way". See README.md.

    ccproject_criteria.py                     the board and its verdict
    ccproject_criteria.py --cell V            each row graded at one axis value
    ccproject_criteria.py --gate              exit 1 unless every criterion is green
    ccproject_criteria.py show ID             one criterion in full
    ccproject_criteria.py add -i ID -r REQ -d DETECTOR
    ccproject_criteria.py update ID -s PASS -m "what was measured" [--cell V]
    ccproject_criteria.py pending|executing|finalize ...   a harness run in flight
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import importlib.util
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

STORE_DIRNAME = ".ccproject"
FILENAME = "criteria.json"
DEFECTS_FILENAME = "defects.json"
DRIVER_FILENAME = "driver.py"
CONFIG_FILENAME = "config.json"
#: What the store's own .gitignore keeps out of the repo: each data file's lock, the single lock
#: older versions took, and an interrupted save's temp file.
IGNORED = (".lock", ".*.lock", "*.tmp")
LOCK_WAIT_SECONDS = 30
HISTORY_DEPTH = 10

#: The hook interface a project driver is written against. A driver states the API it was
#: written for; one for any other is refused, so a changed interface cannot silently mis-grade.
DRIVER_API = 1

#: The project's `.ccproject/driver.py`, once loaded; None when the project has none.
driver = None

#: Covers the sets the existing project boards use; a project narrows it with `statuses`.
DEFAULT_STATUSES = ("PASS", "FAIL", "PARTIAL", "NOT STARTED", "UNKNOWN", "STALE", "BLOCKED",
                    "INCOMPLETE", "SKIP", "PENDING", "NOT RUN")
DEFAULT_GREEN = ("PASS",)
#: The statuses that make a matrix row FAIL when any cell has one; a project whose INCOMPLETE run
#: is a failure widens it with `failing`.
DEFAULT_FAILING = ("FAIL",)

#: A result: what one measurement wrote. Pushed into history when the next one replaces it.
RESULT_FIELDS = ("status", "measured", "window", "evidence", "reason", "run_id", "build",
                 "recorded")

ORDERED = ("id", "phase", "status", "requirement", "detector", "why", "source", "invariant",
           "budget_s", "measured", "window", "evidence", "reason", "run_id", "build", "recorded",
           "values", "history")

COLOURS = {"green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m", "end": "\033[0m"}

lock_handle = None


def project_store() -> Path:
    """The nearest existing `.ccproject/` at or above the cwd, else `<cwd>/.ccproject/`."""
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / STORE_DIRNAME).is_dir():
            return directory / STORE_DIRNAME
    return here / STORE_DIRNAME


config_cache = None


def config() -> dict:
    """`.ccproject/config.json`: where the project's files are (`store`) and which of its own
    tools hold its release state (`external`). Empty when the project has none."""
    global config_cache
    if config_cache is None:
        path = project_store() / CONFIG_FILENAME
        config_cache = {}
        if path.is_file():
            try:
                config_cache = json.loads(path.read_text())
            except ValueError as exc:
                sys.exit(f"criteria: {path} is not valid JSON: {exc}")
            if not isinstance(config_cache, dict):
                sys.exit(f"criteria: {path} is not a JSON object")
    return config_cache


def data_path(kind: str, default: str) -> Path:
    """The file holding `kind` ("criteria" or "defects"): the one `store` names, relative to the
    project root, else `.ccproject/<default>`. A named file must be inside the project, because
    the release state travels with the repository."""
    named = (config().get("store") or {}).get(kind)
    if not named:
        return project_store() / default
    root = project_store().parent.resolve()
    path = (root / named).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        sys.exit(f"criteria: .ccproject/{CONFIG_FILENAME} puts {kind} at {named}, outside the "
                 f"project {root}")
    return path


def external(kind: str):
    """The project's own tool for `kind`, when `.ccproject/config.json` declares one."""
    return (config().get("external") or {}).get(kind) or None


def refuse_external() -> None:
    """A project that keeps its criteria in a tool of its own is not graded here. Running anyway
    would answer from a store that tool does not write -- an answer that looks current and is
    not -- and an `import` would start exactly such a store."""
    own = external("criteria")
    if own:
        sys.exit(f"criteria: this project keeps its criteria in {own}; use that. "
                 f"(.ccproject/{CONFIG_FILENAME} declares it, so ccproject_criteria.py does not "
                 f"run here.)")


def board_path() -> Path:
    return data_path("criteria", FILENAME)


def lock_path(path: Path) -> Path:
    """Each data file's lock sits beside it as `.<name>.lock`, so a project tool that writes the
    same file under the same lock name excludes this one's writes and is excluded by them."""
    return path.with_name("." + path.name + ".lock")


def ignore_store(store: Path) -> None:
    """The data travels with the repo; the locks and an interrupted save's temp file do not."""
    ignore = store / ".gitignore"
    have = ignore.read_text().split("\n") if ignore.exists() else []
    missing = [pattern for pattern in IGNORED if pattern not in have]
    if missing:
        text = "\n".join(line for line in have if line) + "\n" if any(have) else ""
        ignore.write_text(text + "\n".join(missing) + "\n")


def take_lock() -> None:
    global lock_handle
    store = project_store()
    store.mkdir(parents=True, exist_ok=True)
    ignore_store(store)
    target = board_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path(target)
    lock_handle = lock.open("w")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    while True:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                sys.exit("criteria: %s has been held for %ds -- far longer than a write takes, "
                         "so the holder is wedged rather than busy." % (lock, LOCK_WAIT_SECONDS))
            if not announced:
                print("criteria: waiting for another writer to finish…", file=sys.stderr)
                announced = True
            time.sleep(0.05)


def load() -> dict:
    path = board_path()
    if not path.is_file():
        return {"criteria": []}
    try:
        with path.open() as handle:
            data = json.load(handle)
    except ValueError as exc:
        sys.exit(f"criteria: {path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("criteria", []), list):
        sys.exit(f'criteria: {path} is not a {{"criteria": [...]}} document')
    data.setdefault("criteria", [])
    return data


def save(data: dict) -> None:
    path = board_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def load_driver():
    """Import `.ccproject/driver.py` if the project has one.

    The driver is project code, trusted as the project's own tests are. The tool module is
    handed to it as `driver.ccproject`, so a hook or a driver command can call `save`, `find`,
    `grade` and the rest rather than re-implementing them.
    """
    path = project_store() / DRIVER_FILENAME
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("ccproject_driver", path)
    module = importlib.util.module_from_spec(spec)
    module.ccproject = sys.modules[__name__]
    try:
        spec.loader.exec_module(module)
    except Exception:
        traceback.print_exc()
        sys.exit(f"criteria: {path} failed to load (traceback above)")
    api = getattr(module, "API", None)
    if api != DRIVER_API:
        sys.exit(f"criteria: {path} declares API = {api!r}; this tool speaks API {DRIVER_API}")
    return module


def hook(name):
    """The driver's hook by that name, or None when there is no driver or it lacks one."""
    return getattr(driver, name, None) if driver is not None else None


def load_defects() -> dict:
    """Read-only, no lock: the defects file is replaced atomically, so a read is never torn.
    None when the project keeps no defect queue, or keeps it in a tool of its own."""
    if external("defects"):
        return None
    path = data_path("defects", DEFECTS_FILENAME)
    if not path.is_file():
        return None
    try:
        with path.open() as handle:
            data = json.load(handle)
    except ValueError as exc:
        sys.exit(f"criteria: {path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return {"defects": []}
    data.setdefault("defects", [])
    return data


def defect_matches(entry: dict, field: str, values):
    have = entry.get(field)
    if have is None or have == "":
        return None
    wanted = {str(v).lower() for v in (values if isinstance(values, list) else [values])}
    return str(have).lower() in wanted


def defect_blocks(defects: dict, entry: dict, args) -> bool:
    """The defect queue's own gate, read here so the board's verdict counts it.

    Must agree with ccproject_defects.py's `blocks`: the driver's `blocks` hook first, then
    `gate_exclude` (a match does not block; a missing field still does) and `gate_require` (a
    record blocks only with a listed value).
    """
    decide = hook("blocks")
    if decide is not None:
        verdict = decide(defects, entry, args)
        if verdict is not None:
            return bool(verdict)
    for field, values in (defects.get("gate_exclude") or {}).items():
        if defect_matches(entry, field, values):
            return False
    for field, values in (defects.get("gate_require") or {}).items():
        if not defect_matches(entry, field, values):
            return False
    return True


def blocked_by(queue) -> dict:
    """criterion id -> ids of the open defects that name it (queue: load_defects())."""
    named: dict = {}
    for entry in (queue or {}).get("defects", []):
        for criterion in entry.get("criteria") or []:
            named.setdefault(criterion, []).append(str(entry.get("id", "?")))
    return named


def today() -> str:
    return datetime.date.today().isoformat()


def status_of(record: dict) -> str:
    return str(record.get("status") or "UNKNOWN").strip().upper()


def statuses(data: dict) -> list:
    return [str(s).strip().upper() for s in (data.get("statuses") or DEFAULT_STATUSES)]


def green_set(data: dict) -> set:
    return {str(s).strip().upper() for s in (data.get("green") or DEFAULT_GREEN)}


def failing_set(data: dict) -> set:
    return {str(s).strip().upper() for s in (data.get("failing") or DEFAULT_FAILING)}


def cell_key(data: dict):
    axis = data.get("axis")
    return f"per_{axis}" if axis else None


def is_matrix(data: dict, row: dict) -> bool:
    key = cell_key(data)
    return bool(key) and isinstance(row.get(key), dict)


def values_for(data: dict, row: dict) -> list:
    """The axis values a matrix row must pass: the driver's `applies` hook when it answers,
    else the row's own `values`, else every declared axis value."""
    applies = hook("applies")
    if applies is not None:
        answer = applies(data, row)
        if answer is not None:
            return list(answer)
    return list(row.get("values") or data.get("axis_values") or [])


def combine(data: dict, seen: list, row=None) -> str:
    """Cell statuses -> row status: every one green -> PASS; any failing one (`failing`, default
    FAIL) -> FAIL; else PARTIAL. With no values to judge nothing can be complete, so it is never
    PASS. The driver's `roll_up(data, row, statuses)` answers first; None means this rule."""
    custom = hook("roll_up")
    if custom is not None and row is not None:
        answer = custom(data, row, list(seen))
        if answer is not None:
            return str(answer).strip().upper()
    green = green_set(data)
    if seen and all(s in green for s in seen):
        return "PASS"
    failing = failing_set(data)
    if any(s in failing for s in seen):
        return "FAIL"
    return "PARTIAL"


def derive(data: dict, row: dict) -> None:
    """Store a matrix row's status from its cells, as recorded."""
    cells = row[cell_key(data)]
    row["status"] = combine(data, [status_of(cells[v]) if v in cells else "NOT RUN"
                                   for v in values_for(data, row)], row)


def judged(data: dict, row: dict, value, record: dict, build) -> str:
    """A record's status as graded against `build`: a green earned on a different build -- or
    on no recorded build -- is not evidence about this one, so it reads STALE. The stored
    result is never rewritten; this is how the board and the gate read it.

    The driver's `grade` hook answers first (value is None for a plain row, whose record is
    the row itself, and record is None for a cell never written); None from it means "grade it
    the generic way"."""
    custom = hook("grade")
    if custom is not None:
        answer = custom(data, row, value, record, build)
        if answer is not None:
            return str(answer).strip().upper()
    if record is None:
        return "NOT RUN"
    status = status_of(record)
    if build is not None and status in green_set(data) and str(record.get("build", "")) != build:
        return "STALE"
    return status


def grade(data: dict, row: dict, build=None):
    """(row status, {value: cell status}) as graded for `build` (None = as recorded)."""
    if not is_matrix(data, row) or not row[cell_key(data)]:
        #: A matrix row with no cell recorded yet still says what was set on it (NOT STARTED,
        #: UNKNOWN …); judging it from zero cells would turn every one of those into PARTIAL.
        return judged(data, row, None, row, build), {}
    cells = row[cell_key(data)]
    per = {v: judged(data, row, v, cells.get(v), build) for v in values_for(data, row)}
    return combine(data, list(per.values()), row), per


def find(data: dict, wanted: str) -> dict:
    wanted = wanted.strip()
    for row in data["criteria"]:
        if str(row.get("id", "")) == wanted:
            return row
    low = wanted.lower()
    hits = [r for r in data["criteria"] if low in str(r.get("id", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"criteria: no criterion matches {wanted!r}")
    sys.exit("criteria: %r matches %d criteria:\n  %s"
             % (wanted, len(hits), "\n  ".join(str(r.get("id")) for r in hits)))


def flat(text, width: int) -> str:
    line = " ".join(str(text).split())
    return line[:width - 1] + "…" if len(line) > width else line


def wrap(text, width: int, indent: str) -> str:
    out, line = [], ""
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return ("\n" + indent).join(out)


def shown(value) -> str:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return ", ".join(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def paint(status: str, data: dict, colour: bool) -> str:
    if not colour:
        return status
    if status in green_set(data):
        tone = "green"
    elif status in failing_set(data) or status in ("FAIL", "BLOCKED"):
        tone = "red"
    else:
        tone = "yellow"
    return COLOURS[tone] + status + COLOURS["end"]


def tally(data: dict, row: dict, per: dict) -> str:
    """`3/10 class`, then which values failed and which went stale, from the graded cells."""
    wanted = values_for(data, row)
    green = green_set(data)
    passed = sum(1 for v in wanted if per.get(v) in green)
    text = f"{passed}/{len(wanted)} {data.get('axis')}"
    for label in ("FAIL", "STALE"):
        hit = [v for v in wanted if per.get(v) == label]
        if hit:
            text += f" — {label}: " + ", ".join(hit)
    return text


def snapshot(record: dict) -> dict:
    return {k: record[k] for k in RESULT_FIELDS if record.get(k) not in (None, "")}


def push_history(record: dict) -> None:
    """Keep the result being replaced, newest first, when one was ever recorded."""
    before = snapshot(record)
    if "recorded" not in before:
        return
    record["history"] = ([before] + list(record.get("history") or []))[:HISTORY_DEPTH]


def check_status(data: dict, status: str) -> str:
    status = status.strip().upper()
    if status not in statuses(data):
        sys.exit("criteria: unknown status %r; the statuses are: %s"
                 % (status, ", ".join(statuses(data))))
    return status


def notify(data: dict, row: dict, value, record: dict, args) -> None:
    """Hand a written result to the driver's `on_result` hook, before the board is saved, so a
    project can keep its own run log or project data in step with the write. Called with the
    board lock held."""
    written = hook("on_result")
    if written is not None:
        written(data, row, value, record, args)


def target_of(data: dict, row: dict, cell):
    """The record a result is written to: the row, or one of its cells."""
    if cell is None:
        return row
    if not cell_key(data):
        sys.exit("criteria: --cell needs an axis; declare one with "
                 "`ccproject_criteria.py axis NAME VALUE...`")
    if not is_matrix(data, row):
        #: The first per-value result is what makes a row a matrix row: a board whose rows
        #: were marked per-value without ever being given a cell map (as boards written by
        #: hand often are) still takes the write rather than refusing the harness.
        row[cell_key(data)] = {}
    wanted = values_for(data, row)
    if not wanted:
        sys.exit(f"criteria: no {data.get('axis')} values are declared; set them with "
                 f"`ccproject_criteria.py axis {data.get('axis')} VALUE...`")
    if cell not in wanted:
        sys.exit("criteria: %r is not a %s of %s; the values are: %s"
                 % (cell, data.get("axis"), row["id"], ", ".join(wanted)))
    return row[cell_key(data)].setdefault(cell, {})


def at_cell(data: dict, rows: list, cell, build) -> tuple:
    """The rows that apply at one axis value, each graded there: (rows, [(status, {}, record)]).

    A matrix row applies when `cell` is one of its values and is graded on that cell alone; a
    plain row is not per-value, so it applies everywhere and keeps its own status."""
    key = cell_key(data)
    if not key:
        sys.exit("criteria: --cell needs an axis; declare one with "
                 "`ccproject_criteria.py axis NAME VALUE...`")
    kept, graded = [], []
    for row in rows:
        if isinstance(row.get(key), dict):
            if cell not in values_for(data, row):
                continue
            record = row[key].get(cell)
            graded.append((judged(data, row, cell, record, build), {}, record or {}))
        else:
            graded.append((grade(data, row, build)[0], {}, row))
        kept.append(row)
    return kept, graded


def cmd_board(data: dict, args) -> int:
    rows = data["criteria"]
    green = green_set(data)
    cell = getattr(args, "cell", None)
    if cell is not None:
        rows, graded = at_cell(data, rows, cell, args.build)
    else:
        graded = [grade(data, r, args.build) + (r,) for r in rows]
    good = sum(1 for status, per, shown_record in graded if status in green)
    queue = load_defects()
    #: "No open blocking defect" is one of the release criteria, so when the project keeps a
    #: defect queue the verdict counts it as a line of its own. `"defects_gate": false` in the
    #: board turns that off for a project whose queue is not a release bar.
    blocking = None
    if queue is not None and data.get("defects_gate", True) is not False:
        blocking = [str(e.get("id", "?")) for e in queue["defects"]
                    if defect_blocks(queue, e, args)]
        good += 0 if blocking else 1
    total = len(rows) + (0 if blocking is None else 1)
    met = bool(rows) and good == total
    if args.json:
        report = {"criteria": rows, "verdict": {"met": met, "green": good, "total": total}}
        if blocking is not None:
            report["verdict"]["blocking_defects"] = blocking
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if (args.gate and not met) else 0
    if args.gaps:
        return cmd_gaps(data)
    colour = sys.stdout.isatty() and not args.no_colour
    named = blocked_by(queue)
    width = max([len(str(r.get("id", ""))) for r in rows] + [10])
    phase = None
    for row, (status, per, shown_record) in zip(rows, graded):
        if args.verbose and row.get("phase") != phase:
            phase = row.get("phase")
            print(f"\n== {phase or '(no phase)'} ==")
        extra = []
        if per:
            extra.append(tally(data, row, per))
        else:
            if shown_record.get("window"):
                extra.append("[" + flat(shown_record["window"], 40) + "]")
            if shown_record.get("measured"):
                extra.append(flat(shown_record["measured"], 60))
        if named.get(row.get("id")):
            extra.append("blocked by " + ", ".join(named[row["id"]]))
        padding = " " * max(0, 11 - len(status))
        print("%s%s %-*s  %s" % (paint(status, data, colour), padding, width, row.get("id", "?"),
                                 "  ".join(extra)))
        if args.verbose:
            for key in ("requirement", "why", "detector"):
                if row.get(key):
                    print("    %-11s %s" % (key, wrap(row[key], 88, " " * 16)))
    if blocking is not None:
        status = "FAIL" if blocking else "PASS"
        shown_ids = ", ".join(blocking[:5]) + (f", … (+{len(blocking) - 5})"
                                               if len(blocking) > 5 else "")
        detail = f"{len(blocking)} blocking: {shown_ids}" if blocking else \
            f"{len(queue['defects'])} open, none blocking"
        print("%s%s %-*s  %s" % (paint(status, {"green": ["PASS"]}, colour),
                                 " " * (11 - len(status)), width, "no blocking defects", detail))
    if not rows:
        print("no criteria.")
        verdict_line = "VERDICT: nothing to judge -- no criteria defined"
    elif met:
        verdict_line = f"VERDICT: every criterion green ({good})"
    else:
        verdict_line = f"VERDICT: {good} of {total} criteria green — bar not met"
    if cell is not None:
        verdict_line += f" — at {data.get('axis')} {cell}"
    if args.build is not None:
        verdict_line += f" — graded on build {args.build}"
    print("\n" + verdict_line)
    return 1 if (args.gate and not met) else 0


def cmd_gaps(data: dict) -> int:
    root = project_store().parent
    gaps = []
    for row in data["criteria"]:
        detector = str(row.get("detector") or "").strip()
        if not detector:
            gaps.append((row.get("id"), "no detector"))
            continue
        script = next((w for w in detector.split() if w.endswith((".py", ".sh"))), None)
        if script and not (root / script).exists():
            gaps.append((row.get("id"), f"detector {script} does not exist under {root}"))
    for criterion, why in gaps:
        print(f"{criterion}: {why}")
    print(f"\n{len(gaps)} gap(s) in {len(data['criteria'])} criteria.")
    return 0


def print_result(record: dict, indent: str) -> None:
    for key in RESULT_FIELDS:
        if record.get(key) not in (None, ""):
            print("%s%-11s %s" % (indent, key, wrap(record[key], 84, indent + " " * 12)))


def cmd_show(data: dict, args) -> int:
    row = find(data, args.id)
    print(row.get("id", "?"))
    for key in ORDERED[1:]:
        if key == "history" or row.get(key) in (None, "", []):
            continue
        print("  %-11s %s" % (key, wrap(shown(row[key]), 88, " " * 14)))
    key = cell_key(data)
    for other, value in row.items():
        if other not in ORDERED and other != key:
            print("  %-11s %s" % (other, wrap(shown(value), 88, " " * 14)))
    if is_matrix(data, row):
        cells = row[key]
        print(f"  {data.get('axis')}:")
        for value in values_for(data, row) + [v for v in cells if v not in values_for(data, row)]:
            cell = cells.get(value)
            #: Graded as the board grades it; the status as written is shown beside it when the
            #: two differ, so a stale or driver-graded cell is not read as its raw record.
            status = judged(data, row, value, cell, getattr(args, "build", None))
            if cell is None:
                print(f"    {value}: {status}")
                continue
            recorded = status_of(cell)
            print(f"    {value}: {status}" + (f" (recorded {recorded})"
                                              if recorded != status else ""))
            print_result({k: v for k, v in cell.items() if k != "status"}, "      ")
    for number, before in enumerate(row.get("history") or [], 1):
        print(f"  history {number}: {status_of(before)}")
        print_result({k: v for k, v in before.items() if k != "status"}, "      ")
    named = blocked_by(load_defects()).get(row.get("id"))
    if named:
        print("  blocked by  " + ", ".join(named))
    return 0


def cmd_add(data: dict, args) -> int:
    if any(str(r.get("id")) == args.id for r in data["criteria"]):
        sys.exit(f"criteria: {args.id} already exists")
    if not args.id or any(ch.isspace() for ch in args.id):
        sys.exit(f"criteria: {args.id!r} is not a usable id (empty or contains whitespace)")
    row = {"id": args.id, "requirement": args.requirement, "detector": args.detector,
           "status": "NOT STARTED"}
    for key in ("phase", "why", "source"):
        if getattr(args, key):
            row[key] = getattr(args, key)
    if args.budget_s is not None:
        row["budget_s"] = args.budget_s
    if args.per_cell:
        if not cell_key(data):
            sys.exit("criteria: --per-cell needs an axis; declare one with "
                     "`ccproject_criteria.py axis NAME VALUE...`")
        row[cell_key(data)] = {}
    data["criteria"].append(row)
    save(data)
    print(f"added {args.id}")
    return 0


def cmd_update(data: dict, args) -> int:
    row = find(data, args.id)
    results = {"status": args.status, "measured": args.measured, "window": args.window,
               "evidence": args.evidence, "reason": args.reason, "run_id": args.run_id,
               "build": args.build}
    results = {k: v for k, v in results.items() if v is not None}
    spec = {"requirement": args.requirement, "detector": args.detector, "why": args.why,
            "phase": args.phase, "budget_s": args.budget_s, "values": args.values}
    spec = {k: v for k, v in spec.items() if v is not None}
    if results and spec:
        sys.exit("criteria: a result (-s/-m/-w/--evidence/--reason/--run-id/--build) and a "
                 "specification change (-r/-d/--why/-p/-b/--values) are separate updates")
    if not results and not spec:
        sys.exit("criteria: nothing to update -- give a result or a specification field")
    if spec:
        if args.cell is not None:
            sys.exit("criteria: --cell applies to a result, not a specification change")
        if "values" in spec:
            unknown = [v for v in spec["values"] if v not in (data.get("axis_values") or [])]
            if not is_matrix(data, row) or unknown:
                sys.exit("criteria: --values must name declared %s values of a matrix row%s"
                         % (data.get("axis") or "axis",
                            (": not declared: " + ", ".join(unknown)) if unknown else ""))
        row.update(spec)
        save(data)
        print(f"updated {row['id']}")
        return 0
    rewrite = hook("before_result")
    if rewrite is not None:
        #: The project's own rules for what a result says -- a time budget overrun that turns a
        #: PASS into a FAIL, a status it records rather than refuses -- applied before the result
        #: is checked or written. The hook edits `results` in place and may exit to refuse it.
        rewrite(data, row, args.cell, results, args)
    if "status" in results:
        results["status"] = check_status(data, results["status"])
    target = target_of(data, row, args.cell)
    if "status" in results:
        #: A new status is a new result: the old one goes to history whole, and none of its
        #: fields may linger beside the new status as if they described it.
        push_history(target)
        for key in RESULT_FIELDS:
            if key not in results:
                target.pop(key, None)
    target.update(results)
    target["recorded"] = today()
    if args.cell is not None:
        derive(data, row)
    notify(data, row, args.cell, target, args)
    save(data)
    where = f" [{args.cell}]" if args.cell is not None else ""
    print(f"{row['id']}{where}: {status_of(target)}"
          + (f" (row {status_of(row)})" if args.cell is not None else ""))
    return 0


def cmd_remove(data: dict, args) -> int:
    row = find(data, args.id)
    if status_of(row) in green_set(data):
        print(f"criteria: warning: {row['id']} was {status_of(row)} -- removing a green "
              f"criterion lowers the bar", file=sys.stderr)
    named = blocked_by(load_defects()).get(row.get("id"))
    if named:
        print("criteria: warning: still named by open defects: " + ", ".join(named),
              file=sys.stderr)
    data["criteria"].remove(row)
    save(data)
    print("- **Criterion `%s` removed** (%s) — %s"
          % (row["id"], flat(row.get("requirement", ""), 120), " ".join(args.why.split())))
    return 0


def cmd_move(data: dict, args) -> int:
    row = find(data, args.id)
    anchor = find(data, args.before or args.after)
    if anchor is row:
        sys.exit("criteria: cannot move a criterion relative to itself")
    data["criteria"].remove(row)
    position = data["criteria"].index(anchor) + (0 if args.before else 1)
    data["criteria"].insert(position, row)
    save(data)
    print(f"moved {row['id']} {'before' if args.before else 'after'} {anchor['id']}")
    return 0


def cmd_axis(data: dict, args) -> int:
    old = data.get("axis")
    if old and old != args.name and any(isinstance(r.get(f"per_{old}"), dict)
                                        for r in data["criteria"]):
        sys.exit(f"criteria: rows already hold per_{old} cells; the axis stays {old!r}")
    data["axis"] = args.name
    data["axis_values"] = list(dict.fromkeys(args.values))
    for row in data["criteria"]:
        if is_matrix(data, row) and row[cell_key(data)]:
            derive(data, row)
    save(data)
    print(f"axis {args.name}: " + ", ".join(data["axis_values"]))
    return 0


def targets(data: dict, run_id=None):
    """Every (row, value, record) whose record is PENDING, optionally for one run; value is
    None when the record is the row itself."""
    for row in data["criteria"]:
        records = [(None, row)]
        if is_matrix(data, row):
            records += list(row[cell_key(data)].items())
        for value, record in records:
            if status_of(record) == "PENDING" and (run_id is None or
                                                   record.get("run_id") == run_id):
                yield row, value, record


def cmd_pending(data: dict, args) -> int:
    rows = [find(data, wanted) for wanted in args.ids]
    for row in rows:
        target = target_of(data, row, args.cell)
        push_history(target)
        for key in RESULT_FIELDS:
            target.pop(key, None)
        target.update({"status": "PENDING", "reason": f"running {args.run_id}",
                       "run_id": args.run_id, "recorded": today()})
        if args.build is not None:
            target["build"] = args.build
        if args.cell is not None:
            derive(data, row)
    save(data)
    print("pending: " + ", ".join(r["id"] for r in rows)
          + (f" [{args.cell}]" if args.cell is not None else ""))
    return 0


def cmd_executing(data: dict, args) -> int:
    row = find(data, args.id)
    target = target_of(data, row, args.cell)
    if status_of(target) != "PENDING" or target.get("run_id") != args.run_id:
        sys.exit(f"criteria: {row['id']} is not pending for run {args.run_id}")
    target["reason"] = f"executing {args.run_id}"
    save(data)
    print(f"executing: {row['id']}" + (f" [{args.cell}]" if args.cell is not None else ""))
    return 0


def cmd_finalize(data: dict, args) -> int:
    """Settle what a run left in flight: started and never reported -> INCOMPLETE, never
    started -> NOT RUN. A run that dies leaves markers; this is what turns them into results."""
    settled, touched, results = 0, [], []
    for row, value, record in list(targets(data, args.run_id)):
        started = str(record.get("reason", "")).startswith("executing")
        record["status"] = "INCOMPLETE" if started else "NOT RUN"
        record["reason"] = ("was executing " if started else "never started ") + \
            str(record.get("run_id", ""))
        record["recorded"] = today()
        settled += 1
        results.append((row, value, record))
        if row not in touched:
            touched.append(row)
    for row in touched:
        if is_matrix(data, row) and row[cell_key(data)]:
            derive(data, row)
    for row, value, record in results:
        notify(data, row, value, record, args)
    if settled:
        save(data)
    print(f"finalized {settled} pending result(s)")
    return 0


def cmd_import(args) -> int:
    source = Path(args.path)
    target = board_path()
    if target.exists():
        sys.exit(f"criteria: {target} already exists; import only starts a board")
    try:
        raw = source.read_bytes()
        data = json.loads(raw)
    except OSError as exc:
        sys.exit(f"criteria: cannot read {source}: {exc}")
    except ValueError as exc:
        sys.exit(f"criteria: {source} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        sys.exit(f'criteria: {source} is not a {{"criteria": [...]}} document')
    target.parent.mkdir(parents=True, exist_ok=True)
    axes = sorted({k[4:] for r in data["criteria"] for k, v in r.items()
                   if k.startswith("per_") and isinstance(v, dict)})
    if not data.get("axis") and len(axes) == 1:
        data["axis"] = axes[0]
        save(data)
        print(f"criteria: matrix rows use per_{axes[0]}; axis set to {axes[0]!r}. Declare its "
              f"values with `ccproject_criteria.py axis {axes[0]} VALUE...`", file=sys.stderr)
    else:
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, target)
        if len(axes) > 1 and not data.get("axis"):
            print("criteria: rows use several cell maps (%s); only one axis is supported, "
                  "declare it with `axis`" % ", ".join("per_" + a for a in axes), file=sys.stderr)
    print(f"imported {len(data['criteria'])} criteria into {target}")
    return 0


def peek_axis():
    """The declared axis, read before argument parsing so `--<axis>` can alias `--cell`."""
    try:
        with board_path().open() as handle:
            axis = json.load(handle).get("axis")
    except (OSError, ValueError, AttributeError):
        return None
    return axis if isinstance(axis, str) and axis.replace("_", "").replace("-", "").isalnum() \
        else None


#: Option names already taken in a parser that carries --cell; an axis by one of these names
#: gets no alias rather than a clash.
RESERVED = {"cell", "at", "status", "measured", "window", "evidence", "reason", "run-id", "build",
            "requirement", "detector", "why", "phase", "budget-s", "values", "help", "id", "ids",
            "verbose", "gaps", "gate", "json", "no-colour"}


def add_cell(parser, axis) -> None:
    """`--cell VALUE`, also spelled `--at VALUE` and `--<axis> VALUE` (e.g. `--class mage`)."""
    names = ["--cell", "--at"]
    if axis and axis not in RESERVED:
        names.append(f"--{axis}")
    parser.add_argument(*names, dest="cell", metavar="VALUE",
                        help="the axis value of a matrix row; spelled " + " or ".join(names))


def build_parser(axis=None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccproject_criteria.py",
        description="What the project must achieve (.ccproject/criteria.json). With no "
                    "subcommand, print the board and its verdict.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="requirement, why and detector, grouped by phase")
    parser.add_argument("--gaps", action="store_true",
                        help="criteria with no detector, or whose detector script is missing")
    parser.add_argument("--gate", action="store_true", help="exit 1 unless every row is green")
    parser.add_argument("--build", metavar="B",
                        help="grade against build B: a green recorded on any other build reads "
                             "STALE")
    parser.add_argument("--json", action="store_true", help="print the criteria as JSON")
    parser.add_argument("--no-colour", action="store_true")
    add_cell(parser, axis)
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("show", help="one criterion in full")
    p.add_argument("id")

    p = sub.add_parser("add", help="add a criterion")
    p.add_argument("-i", "--id", required=True)
    p.add_argument("-r", "--requirement", required=True)
    p.add_argument("-d", "--detector", required=True, help="what measures it")
    p.add_argument("-p", "--phase")
    p.add_argument("-b", "--budget-s", type=float, help="seconds the detector may take")
    p.add_argument("--why")
    p.add_argument("--source", help="where the requirement came from")
    p.add_argument("--per-cell", action="store_true",
                   help="a matrix row: one result per axis value")

    p = sub.add_parser("update", help="record a result, or change the specification")
    p.add_argument("id")
    p.add_argument("-s", "--status")
    p.add_argument("-m", "--measured")
    p.add_argument("-w", "--window", help="what the measurement covered")
    p.add_argument("--evidence")
    p.add_argument("--reason")
    p.add_argument("--run-id")
    p.add_argument("--build", help="the build the result was measured on")
    add_cell(p, axis)
    p.add_argument("-r", "--requirement")
    p.add_argument("-d", "--detector")
    p.add_argument("--why")
    p.add_argument("-p", "--phase")
    p.add_argument("-b", "--budget-s", type=float)
    p.add_argument("--values", nargs="+", metavar="VALUE",
                   help="the axis values this matrix row must pass (default: all)")

    p = sub.add_parser("remove", help="remove a criterion; prints the CHANGELOG line")
    p.add_argument("id")
    p.add_argument("--why", required=True)

    p = sub.add_parser("move", help="reorder the board")
    p.add_argument("id")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--before", metavar="ID")
    where.add_argument("--after", metavar="ID")

    p = sub.add_parser("axis", help="declare the matrix axis and its values")
    p.add_argument("name")
    p.add_argument("values", nargs="+")

    p = sub.add_parser("pending", help="mark criteria as queued for a run")
    p.add_argument("ids", nargs="+")
    p.add_argument("--run-id", required=True)
    p.add_argument("--build")
    add_cell(p, axis)

    p = sub.add_parser("executing", help="mark a pending criterion as running")
    p.add_argument("id")
    p.add_argument("--run-id", required=True)
    add_cell(p, axis)

    p = sub.add_parser("finalize", help="settle results a run left pending")
    p.add_argument("--run-id")

    p = sub.add_parser("import", help="start this project's board from an existing criteria.json")
    p.add_argument("path")
    extend_parser(parser, sub, "criteria")
    return parser


def extend_parser(parser, sub, tool: str) -> None:
    """Let the driver add board flags (`options(parser)`) and subcommands (`commands(sub)`).

    A driver subcommand marks itself with `set_defaults(driver_command=fn)`; `fn(data, args)`
    runs with the lock held and returns the exit code. A driver may add, never replace: a
    built-in subcommand or flag it collides with is refused rather than silently shadowed.
    """
    builtin = dict(sub.choices)
    try:
        if hook("options") is not None:
            hook("options")(parser)
        if hook("commands") is not None:
            hook("commands")(sub)
    except argparse.ArgumentError as exc:
        sys.exit(f"{tool}: the driver's options collide with the tool's own: {exc}")
    replaced = [name for name, built in builtin.items() if sub.choices.get(name) is not built]
    if replaced:
        sys.exit(f"{tool}: the driver redefines built-in subcommand(s): {', '.join(replaced)}")


COMMANDS = {"show": cmd_show, "add": cmd_add, "update": cmd_update, "remove": cmd_remove,
            "move": cmd_move, "axis": cmd_axis, "pending": cmd_pending,
            "executing": cmd_executing, "finalize": cmd_finalize}
MUTATORS = ("add", "update", "remove", "move", "axis", "pending", "executing", "finalize",
            "import")


def main(argv=None) -> int:
    global driver
    refuse_external()
    driver = load_driver()
    args = build_parser(peek_axis()).parse_args(argv)
    command = getattr(args, "driver_command", None)
    #: A driver subcommand that only reads (`set_defaults(driver_readonly=True)`) runs without the
    #: lock, as the board does: saves are atomic, and a long report must not stall a writer.
    if args.cmd in MUTATORS or (command is not None and
                                not getattr(args, "driver_readonly", False)):
        take_lock()
    if args.cmd == "import":
        return cmd_import(args)
    data = load()
    if command is not None:
        return int(command(data, args) or 0)
    if args.cmd is None:
        return cmd_board(data, args)
    return COMMANDS[args.cmd](data, args)


if __name__ == "__main__":
    sys.exit(main())
