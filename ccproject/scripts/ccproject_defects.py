#!/usr/bin/env python3
"""ccproject_defects.py -- the project's defect queue.

The queue is what is broken and still needs work. A defect is open from `add` until `remove`
takes it out with the reason it was fixed or disproved; there is no status field, because being
in the file IS the status.

Data: `.ccproject/defects.json` in the project -- the nearest directory at or above the cwd that
already holds a `.ccproject/`, else the cwd itself (created on the first write). Format:

    {"defects": [{"id", "severity", "opened", "updated", "summary", "evidence", "next",
                  "criteria": [criterion ids]}, ...],
     "gate_exclude": {"<field>": [values that do not block]},
     "gate_require": {"<field>": [values a record needs to block]}}

Fields this tool does not know are kept on every save and printed by `show`, so a queue written
by an older or project-specific tool loads unchanged. The key `"project"` -- on the queue and on
any record -- is reserved for the project's own data: this tool never reads or rewrites it.

A project extends the tool with `.ccproject/driver.py` (shared with ccproject_criteria.py):
`API = 1`, plus any of `blocks(data, entry, args)` -> bool or None, `options(parser)`, and
`commands(subparsers)`. See README.md.

    ccproject_defects.py                     the queue, severity order, one line each
    ccproject_defects.py -d                  with each entry's summary and next step
    ccproject_defects.py --gate              only what blocks; exit 1 while anything does
    ccproject_defects.py show ID             one entry (id or unique substring)
    ccproject_defects.py add -m SUMMARY ...  open a defect
    ccproject_defects.py update ID ...       change one
    ccproject_defects.py remove ID --why ... take it out; prints the CHANGELOG line
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import importlib.util
import json
import os
import re
import signal
import sys
import time
import traceback
from pathlib import Path

#: `| head` closes the pipe early; die quietly by the signal instead of a BrokenPipeError trace.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

SEVERITIES = ("critical", "high", "major", "medium", "minor")
SEPARATOR = " ===== "
STORE_DIRNAME = ".ccproject"
FILENAME = "defects.json"

#: A minted id longer than this stops being a name and becomes the summary again. The limit is a
#: refusal, never a truncation: cutting an id silently would break every reference already made
#: to it by name.
MAX_ID = 80

#: A save takes milliseconds, so anything still holding the lock after this long is wedged rather
#: than busy -- and saying so beats blocking a session forever.
LOCK_WAIT_SECONDS = 30

#: Shown first by `show`, in this order; every other key follows as stored.
ORDERED = ("id", "severity", "opened", "updated", "summary", "evidence", "next", "criteria")

#: Held until the process exits; binding it keeps the handle (and the lock) from being dropped.
lock_handle = None

DRIVER_FILENAME = "driver.py"
#: The hook interface a project driver is written against; a driver for any other is refused.
DRIVER_API = 1

#: The project's `.ccproject/driver.py`, once loaded; None when the project has none.
driver = None


def project_store() -> Path:
    """The nearest existing `.ccproject/` at or above the cwd, else `<cwd>/.ccproject/`.

    Walking up to an EXISTING store means a command run from a subdirectory writes the project's
    queue rather than starting a stray second one there.
    """
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / STORE_DIRNAME).is_dir():
            return directory / STORE_DIRNAME
    return here / STORE_DIRNAME


def ledger_path() -> Path:
    return project_store() / FILENAME


def take_lock() -> None:
    """Exclusive for the whole read-modify-write: the window that loses a record opens at load."""
    global lock_handle
    store = project_store()
    store.mkdir(parents=True, exist_ok=True)
    ignore = store / ".gitignore"
    if not ignore.exists():
        #: The data travels with the repo; the lock and an interrupted save's temp file do not.
        ignore.write_text(".lock\n*.tmp\n")
    lock_handle = (store / ".lock").open("w")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    announced = False
    while True:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                sys.exit("defects: %s has been held for %ds -- far longer than a write takes, so "
                         "the holder is wedged rather than busy." % (store / ".lock",
                                                                     LOCK_WAIT_SECONDS))
            if not announced:
                print("defects: waiting for another writer to finish…", file=sys.stderr)
                announced = True
            time.sleep(0.05)


def load_driver():
    """Import `.ccproject/driver.py` if the project has one -- the same file
    ccproject_criteria.py loads. The tool module is handed to it as `driver.ccproject`."""
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
        sys.exit(f"defects: {path} failed to load (traceback above)")
    api = getattr(module, "API", None)
    if api != DRIVER_API:
        sys.exit(f"defects: {path} declares API = {api!r}; this tool speaks API {DRIVER_API}")
    return module


def hook(name):
    """The driver's hook by that name, or None when there is no driver or it lacks one."""
    return getattr(driver, name, None) if driver is not None else None


def load() -> dict:
    path = ledger_path()
    if not path.is_file():
        return {"defects": []}
    try:
        with path.open() as handle:
            data = json.load(handle)
    except ValueError as exc:
        sys.exit(f"defects: {path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("defects", []), list):
        sys.exit(f'defects: {path} is not a {{"defects": [...]}} document')
    data.setdefault("defects", [])
    return data


def save(data: dict) -> None:
    """Temp file then rename, so a concurrent reader sees the old queue or the new, never half."""
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def today() -> str:
    return datetime.date.today().isoformat()


def text_of(value) -> str:
    """Older records keep evidence/next as a list of entries; read them as one text."""
    if isinstance(value, list):
        return SEPARATOR.join(str(item) for item in value)
    return "" if value is None else str(value)


def rank(entry: dict) -> tuple:
    severity = str(entry.get("severity", "")).lower()
    position = SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)
    return (position, str(entry.get("id", "")))


def matches(entry: dict, field: str, values):
    """None when the record has no value for `field`, else whether its value is in `values`."""
    have = entry.get(field)
    if have is None or have == "":
        return None
    wanted = {str(v).lower() for v in (values if isinstance(values, list) else [values])}
    return str(have).lower() in wanted


def blocks(entry: dict, data: dict, args=None) -> bool:
    """Whether a record holds the gate shut: the driver's `blocks(data, entry, args)` hook
    when it answers (None means "decide the generic way"), else the project's two filters.

    `gate_exclude` {field: values}: a record whose field has one of the values does not block.
    A missing field never excludes, so a record nobody has classified still blocks.

    `gate_require` {field: values}: a record blocks only if its field has one of the values. A
    missing field does not satisfy it -- a project that requires a rating is saying unrated
    records are not yet release blockers, and the list shows them separately.
    """
    decide = hook("blocks")
    if decide is not None:
        verdict = decide(data, entry, args)
        if verdict is not None:
            return bool(verdict)
    for field, values in (data.get("gate_exclude") or {}).items():
        if matches(entry, field, values):
            return False
    for field, values in (data.get("gate_require") or {}).items():
        if not matches(entry, field, values):
            return False
    return True


def unrated(entry: dict, data: dict) -> bool:
    """Missing a field `gate_require` asks for, and so passed by the gate without a rating."""
    return any(matches(entry, field, values) is None
               for field, values in (data.get("gate_require") or {}).items())


def check_id(new_id: str) -> str:
    if not new_id or any(ch.isspace() for ch in new_id):
        sys.exit(f"defects: {new_id!r} is not a usable id (empty or contains whitespace)")
    if len(new_id) > MAX_ID:
        sys.exit("defects: the id is %d characters and the limit is %d:\n  %s\n"
                 "Choose a shorter one with --id: name the defect, do not restate the summary."
                 % (len(new_id), MAX_ID, new_id))
    return new_id


def mint(summary: str, taken) -> str:
    """`D-` plus the summary's first nine words, so the id says what is wrong."""
    words = re.findall(r"[A-Za-z0-9]+", summary.upper())[:9]
    base = "D-" + "-".join(words) if words else "D-UNNAMED"
    minted, suffix = base, 2
    while minted in taken:
        minted = f"{base}-{suffix}"
        suffix += 1
    return check_id(minted)


def find(data: dict, wanted: str) -> dict:
    """By exact id, else by unique case-insensitive substring."""
    wanted = wanted.strip()
    for entry in data["defects"]:
        if str(entry.get("id", "")) == wanted:
            return entry
    low = wanted.lower()
    hits = [e for e in data["defects"] if low in str(e.get("id", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"defects: no entry matches {wanted!r}")
    sys.exit("defects: %r matches %d entries:\n  %s"
             % (wanted, len(hits), "\n  ".join(str(e.get("id")) for e in hits)))


def flat(text, width: int) -> str:
    line = " ".join(text_of(text).split())
    return line[:width - 1] + "…" if len(line) > width else line


def wrap(text, width: int, indent: str) -> str:
    out, line = [], ""
    for word in text_of(text).split():
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


def show_one(entry: dict) -> None:
    print(entry.get("id", "?"))
    for key in ORDERED[1:]:
        if entry.get(key) in (None, "", []):
            continue
        value = entry[key]
        text = shown(value) if key == "criteria" else text_of(value)
        print("  %-10s %s" % (key, wrap(text, 88, " " * 13)))
    for key, value in entry.items():
        if key not in ORDERED:
            print("  %-10s %s" % (key, wrap(shown(value), 88, " " * 13)))


def cmd_list(data: dict, args) -> int:
    entries = sorted(data["defects"], key=rank)
    total = len(entries)
    if args.severity:
        entries = [e for e in entries if str(e.get("severity", "")).lower() == args.severity]
    unrated_count = 0
    if args.gate:
        candidates = [e for e in entries
                      if not any(matches(e, f, v) for f, v in
                                 (data.get("gate_exclude") or {}).items())]
        unrated_count = sum(1 for e in candidates if unrated(e, data))
        entries = [e for e in entries if blocks(e, data, args)]
    verdict = 1 if (args.gate and entries) else 0
    if args.json:
        json.dump({"defects": entries}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return verdict
    note = ("NOTE: %d more record(s) lack a field gate_require asks for (%s), so the gate "
            "passes them unrated; rate each from its evidence."
            % (unrated_count, ", ".join(data.get("gate_require") or {}))) if unrated_count else ""
    if not entries:
        if args.gate:
            print("nothing in the queue blocks (%d open overall)." % total)
            if note:
                print(note)
        else:
            print("no open defects." if not total else "no open defects match.")
        return verdict
    for entry in entries:
        severity = str(entry.get("severity", "unset"))
        if args.detail:
            print("%-8s %s" % (severity, entry.get("id", "?")))
            for key in ("summary", "next"):
                if entry.get(key):
                    print("         %-8s %s" % (key, wrap(entry[key], 88, " " * 18)))
            if entry.get("criteria"):
                print("         %-8s %s" % ("criteria", shown(entry["criteria"])))
        else:
            print("%-8s %-44s %s" % (severity, entry.get("id", "?"), flat(entry.get("summary"), 70)))
    noun = "blocks" if args.gate else "open"
    if len(entries) == total:
        print("\n%d %s." % (len(entries), noun))
    else:
        print("\n%d shown, %d open overall." % (len(entries), total))
    if note:
        print(note)
    return verdict


def cmd_show(data: dict, args) -> int:
    show_one(find(data, args.id))
    return 0


def cmd_add(data: dict, args) -> int:
    taken = {str(e.get("id")) for e in data["defects"]}
    if args.id:
        new_id = check_id(args.id)
        if new_id in taken:
            sys.exit(f"defects: {new_id} already exists")
    else:
        new_id = mint(args.summary, taken)
    entry = {"id": new_id, "severity": args.severity, "opened": today(), "summary": args.summary}
    if args.evidence:
        entry["evidence"] = args.evidence
    if args.next:
        entry["next"] = args.next
    if args.criterion:
        entry["criteria"] = list(dict.fromkeys(args.criterion))
    data["defects"].append(entry)
    save(data)
    print(f"added {new_id}")
    return 0


def append_text(entry: dict, key: str, text: str) -> None:
    have = entry.get(key)
    if isinstance(have, list):
        have.append(text)
    elif have:
        entry[key] = str(have) + SEPARATOR + text
    else:
        entry[key] = text


def cmd_update(data: dict, args) -> int:
    entry = find(data, args.id)
    changed = False
    if args.severity:
        entry["severity"] = args.severity
        changed = True
    if args.summary:
        entry["summary"] = args.summary
        changed = True
    for key, value in (("evidence", args.evidence), ("next", args.next)):
        if value is None:
            continue
        if text_of(entry.get(key)).strip() and not args.replace:
            sys.exit(f"defects: {entry['id']} already has {key}; -W/-a appends to it, "
                     f"--replace overwrites it")
        entry[key] = value
        changed = True
    for key, value in (("evidence", args.append_evidence), ("next", args.append_next)):
        if value:
            append_text(entry, key, value)
            changed = True
    if args.criterion or args.drop_criterion:
        named = list(entry.get("criteria") or [])
        for criterion in args.criterion or []:
            if criterion not in named:
                named.append(criterion)
        for criterion in args.drop_criterion or []:
            if criterion not in named:
                sys.exit(f"defects: {entry['id']} does not name criterion {criterion!r}")
            named.remove(criterion)
        if named:
            entry["criteria"] = named
        else:
            entry.pop("criteria", None)
        changed = True
    if not changed:
        sys.exit("defects: nothing to update -- give at least one field")
    entry["updated"] = today()
    save(data)
    print(f"updated {entry['id']}")
    return 0


def cmd_remove(data: dict, args) -> int:
    entry = find(data, args.id)
    data["defects"].remove(entry)
    save(data)
    disposition = "fixed" if args.fixed else "disproved" if args.disproved else ""
    print(f"removed {entry.get('id')}" + (f" ({disposition})" if disposition else ""),
          file=sys.stderr)
    why = " ".join(args.why.split())
    print("- **%s** — %s%s" % (" ".join(text_of(entry.get("summary", entry.get("id"))).split()),
                               f"{disposition}: " if disposition else "", why))
    return 0


def cmd_rename(data: dict, args) -> int:
    entry = find(data, args.id)
    new_id = check_id(args.new_id)
    if any(str(e.get("id")) == new_id for e in data["defects"] if e is not entry):
        sys.exit(f"defects: {new_id} already exists")
    old = entry.get("id")
    entry["id"] = new_id
    entry["updated"] = today()
    save(data)
    print(f"renamed {old} -> {new_id}")
    return 0


def cmd_gate_filter(data: dict, args) -> int:
    """Set or clear one field of `gate_exclude` / `gate_require`."""
    key = args.cmd.replace("-", "_")
    rules = dict(data.get(key) or {})
    if args.values:
        rules[args.field] = list(dict.fromkeys(args.values))
    elif args.field in rules:
        del rules[args.field]
    else:
        sys.exit(f"defects: {key} has no {args.field!r} to clear")
    if rules:
        data[key] = rules
    else:
        data.pop(key, None)
    save(data)
    print(f"{key}: " + (json.dumps(rules) if rules else "none"))
    return 0


def cmd_import(args) -> int:
    source = Path(args.path)
    target = ledger_path()
    if target.exists():
        sys.exit(f"defects: {target} already exists; import only starts a queue")
    try:
        raw = source.read_bytes()
        data = json.loads(raw)
    except OSError as exc:
        sys.exit(f"defects: cannot read {source}: {exc}")
    except ValueError as exc:
        sys.exit(f"defects: {source} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        sys.exit(f'defects: {source} is not a {{"defects": [...]}} document')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, target)
    print(f"imported {len(data['defects'])} defects into {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccproject_defects.py",
        description="The project's defect queue (.ccproject/defects.json). With no subcommand, "
                    "list it in severity order.")
    parser.add_argument("-d", "--detail", action="store_true", help="show summary and next step")
    parser.add_argument("-s", "--severity", type=str.lower, choices=SEVERITIES,
                        help="only this severity")
    parser.add_argument("--gate", action="store_true",
                        help="only defects that block (gate_exclude applied); exit 1 while any")
    parser.add_argument("--json", action="store_true", help="print the listed records as JSON")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("show", help="one full entry")
    p.add_argument("id")

    p = sub.add_parser("add", help="open a defect")
    p.add_argument("-m", "--summary", required=True)
    p.add_argument("-s", "--severity", type=str.lower, choices=SEVERITIES, default="medium")
    p.add_argument("-w", "--evidence")
    p.add_argument("-n", "--next")
    p.add_argument("--criterion", action="append", metavar="ID",
                   help="a criterion this defect breaks (repeatable)")
    p.add_argument("--id", help="use this id instead of minting one from the summary")

    p = sub.add_parser("update", help="change an entry")
    p.add_argument("id")
    p.add_argument("-s", "--severity", type=str.lower, choices=SEVERITIES)
    p.add_argument("-m", "--summary")
    p.add_argument("-w", "--evidence", help="replace evidence (needs --replace if it has text)")
    p.add_argument("-n", "--next", help="replace next step (needs --replace if it has text)")
    p.add_argument("--replace", action="store_true", help="allow -w/-n to overwrite text")
    p.add_argument("-W", "--append-evidence", metavar="TEXT", help="append to evidence")
    p.add_argument("-a", "--append-next", metavar="TEXT", help="append to next step")
    p.add_argument("--criterion", action="append", metavar="ID", help="name a criterion")
    p.add_argument("--drop-criterion", action="append", metavar="ID", help="stop naming one")

    p = sub.add_parser("remove", help="take a defect out; prints the CHANGELOG line")
    p.add_argument("id")
    p.add_argument("--why", required=True, help="what was measured that disposed of it")
    disposition = p.add_mutually_exclusive_group()
    disposition.add_argument("--fixed", action="store_true")
    disposition.add_argument("--disproved", action="store_true")

    p = sub.add_parser("rename", help="give an entry a new id")
    p.add_argument("id")
    p.add_argument("new_id")

    p = sub.add_parser("gate-exclude",
                       help="set which values of FIELD do not block --gate; no values clears it")
    p.add_argument("field")
    p.add_argument("values", nargs="*")

    p = sub.add_parser("gate-require",
                       help="--gate counts a record only if FIELD has one of these values; "
                            "no values clears it")
    p.add_argument("field")
    p.add_argument("values", nargs="*")

    p = sub.add_parser("import", help="start this project's queue from an existing defects.json")
    p.add_argument("path")
    extend_parser(parser, sub)
    return parser


def extend_parser(parser, sub) -> None:
    """Let the driver add list flags (`options(parser)`) and subcommands (`commands(sub)`).

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
        sys.exit(f"defects: the driver's options collide with the tool's own: {exc}")
    replaced = [name for name, built in builtin.items() if sub.choices.get(name) is not built]
    if replaced:
        sys.exit(f"defects: the driver redefines built-in subcommand(s): {', '.join(replaced)}")


COMMANDS = {"show": cmd_show, "add": cmd_add, "update": cmd_update, "remove": cmd_remove,
            "rename": cmd_rename, "gate-exclude": cmd_gate_filter,
            "gate-require": cmd_gate_filter}
MUTATORS = ("add", "update", "remove", "rename", "gate-exclude", "gate-require", "import")


def main(argv=None) -> int:
    global driver
    driver = load_driver()
    args = build_parser().parse_args(argv)
    command = getattr(args, "driver_command", None)
    if args.cmd in MUTATORS or command is not None:
        take_lock()
    if args.cmd == "import":
        return cmd_import(args)
    data = load()
    if command is not None:
        return int(command(data, args) or 0)
    if args.cmd is None:
        return cmd_list(data, args)
    return COMMANDS[args.cmd](data, args)


if __name__ == "__main__":
    sys.exit(main())
