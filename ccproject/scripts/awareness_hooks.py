#!/usr/bin/env python3
"""ccproject awareness hooks — keep the three-layer docs from rotting.

The awareness docs are only useful if they stay current, and the original
design left that entirely to the model's goodwill (the "Update Protocol"
in SKILL.md). This module makes freshness enforced rather than hoped-for,
using three Claude Code hooks that are registered globally and self-gate
on whether the *current project* actually has awareness infrastructure
(``.claude/awareness/`` present). In every other project they are no-ops.

Subcommands (each is a hook entry point reading the hook JSON on stdin):

  track    PostToolUse(Edit|Write|MultiEdit) — record which source files and
           which awareness docs were touched this session, into a per-session
           ledger under ``.claude/awareness/.state/``.

  sync     Stop — the enforcement point. Three jobs, in order:
             1. Auto-regenerate Layer 3 (structural map) by running
                generate_structural_map.py — pure extraction, no model.
             2. Compute drift: subsystems whose source changed this session
                but whose subsystem doc was NOT edited. If any remain and the
                per-session nudge cap isn't hit, BLOCK the stop and tell the
                model exactly which docs to update (decision: block, mirroring
                ccloop's keepgoing).
             3. When clean (or cap reached), stamp the [AWARENESS]
                last-updated date and allow the stop.

  status   SessionStart — inject a deterministic drift report (which
           subsystems have source newer than their doc) as additionalContext,
           replacing the skill's soft ">7 days" guess with a real signal.

What is and isn't automatable is a hard line: Layer 3 is pure script output,
so it is regenerated outright. Layers 1 and 2 are prose judgment (invariants,
pitfalls, API intent) that no script can author — so the most a hook can do
is refuse to let the model walk away from drift. That is the same philosophy
as ccloop's Stop hook and ccmemory's edit guard.

Every hook is fail-open: any unexpected error exits 0 so a hook bug can
never wedge a session.

Config / escape hatches (env):
  CCPROJECT_NO_ENFORCE=1    disable the blocking Stop behavior (still regens)
  CCPROJECT_NO_AUTOREGEN=1  disable Layer 3 auto-regeneration
  CCPROJECT_MAX_NUDGES=N    cap re-feeds per session before the stop is allowed
                            (default 3; 0 = unlimited)
"""

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

VERSION = "1.0.0"

# Source extensions we consider "code changes that can drift a subsystem doc".
SOURCE_EXTS = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs",
}

DEFAULT_MAX_NUDGES = 3


# ---------------------------------------------------------------------------
# stdin / project resolution
# ---------------------------------------------------------------------------

def read_stdin_json():
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def project_root(hook_input):
    """Project directory for this hook invocation.

    Claude Code passes ``cwd`` in the hook payload; fall back to the
    process cwd. We do NOT walk upward looking for ``.claude`` — the
    awareness system is rooted at the project the session was opened in.
    """
    cwd = hook_input.get("cwd") or os.getcwd()
    return Path(cwd)


def awareness_dir(root):
    """``.claude/awareness`` if this project is bootstrapped, else None."""
    d = root / ".claude" / "awareness"
    return d if d.is_dir() else None


def state_dir(aware):
    d = aware / ".state"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def ledger_path(aware, session_id):
    sd = state_dir(aware)
    if sd is None:
        return None
    sid = session_id or "nosession"
    # keep the filename filesystem-safe
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)
    return sd / f"touched-{sid}.json"


def load_ledger(path):
    if path is None or not path.exists():
        return {"source": [], "docs": [], "regen_done": False, "nudges": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": [], "docs": [], "regen_done": False, "nudges": 0}
    data.setdefault("source", [])
    data.setdefault("docs", [])
    data.setdefault("regen_done", False)
    data.setdefault("nudges", 0)
    return data


def save_ledger(path, data):
    if path is None:
        return
    try:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLAUDE.md subsystem table + [AWARENESS] metadata
# ---------------------------------------------------------------------------

def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def parse_subsystem_table(claude_md):
    """Return [(name, directory)] from the CLAUDE.md "## Subsystems" table.

    The constitution's subsystem table is a real markdown table
    (``| Subsystem | Directory | Purpose |``), which makes it a reliable,
    structured source of the file->subsystem mapping — far more robust than
    parsing the prose file lists inside each subsystem doc.
    """
    if not claude_md.is_file():
        return []
    try:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rows = []
    in_table = False
    header_cols = None
    dir_idx = None
    name_idx = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and "directory" in stripped.lower() and "subsystem" in stripped.lower():
            header_cols = [c.strip().lower() for c in stripped.strip("|").split("|")]
            try:
                dir_idx = header_cols.index("directory")
                name_idx = header_cols.index("subsystem")
            except ValueError:
                header_cols = None
                continue
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                break  # table ended
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue  # separator row
            if name_idx is None or dir_idx is None:
                continue
            if len(cells) <= max(name_idx, dir_idx):
                continue
            name = cells[name_idx].strip("` ")
            directory = cells[dir_idx].strip("` ")
            if not name or name.startswith("{"):
                continue  # template placeholder
            rows.append((name, directory))
    return rows


def stamp_last_updated(claude_md, today):
    """Set ``- **Last updated**: <today>`` in the [AWARENESS] block."""
    if not claude_md.is_file():
        return
    try:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    new, n = re.subn(
        r"(- \*\*Last updated\*\*:\s*)(.*)",
        lambda m: m.group(1) + today,
        text,
        count=1,
    )
    if n and new != text:
        try:
            claude_md.write_text(new, encoding="utf-8")
        except OSError:
            pass


def stamp_structural_map(claude_md, today, token_count):
    if not claude_md.is_file():
        return
    try:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    new, n = re.subn(
        r"(- \*\*Structural map\*\*:\s*)(.*)",
        lambda m: m.group(1) + f"{today}, ~{token_count} tokens",
        text,
        count=1,
    )
    if n and new != text:
        try:
            claude_md.write_text(new, encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# file -> subsystem classification
# ---------------------------------------------------------------------------

def is_source(path):
    return Path(path).suffix.lower() in SOURCE_EXTS


def rel_to_root(root, path):
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return None


def subsystem_for_file(rel, table):
    """Longest-directory-prefix match of a project-relative file to a subsystem."""
    best = None
    best_len = -1
    for name, directory in table:
        d = directory.strip("/").strip()
        if d in ("", "."):
            prefix = ""
        else:
            prefix = d + "/"
        if prefix == "" or rel == d or rel.startswith(prefix):
            if len(prefix) > best_len:
                best = name
                best_len = len(prefix)
    return best


def subsystem_doc(aware, name):
    p = aware / "subsystems" / f"{slug(name)}.md"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# Layer 3 regen
# ---------------------------------------------------------------------------

def regenerate_structural_map(root, aware):
    """Run generate_structural_map.py for ``root``. Returns token estimate or None."""
    if os.environ.get("CCPROJECT_NO_AUTOREGEN") == "1":
        return None
    gen = Path(__file__).resolve().parent / "generate_structural_map.py"
    if not gen.is_file():
        return None
    try:
        subprocess.run(
            [sys.executable, str(gen), str(root)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    mp = aware / "structural-map.md"
    try:
        text = mp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Prefer the generator's own estimate from the map header so the stamped
    # count agrees with the map; fall back to a chars/4 approximation.
    m = re.search(r"\*\*Approximate token count\*\*:\s*([\d,]+)", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return len(text) // 4


# ---------------------------------------------------------------------------
# track  (PostToolUse)
# ---------------------------------------------------------------------------

def cmd_track():
    hook_input = read_stdin_json()
    root = project_root(hook_input)
    aware = awareness_dir(root)
    if aware is None:
        return 0

    tool = hook_input.get("tool_name", "")
    if tool not in ("Edit", "Write", "MultiEdit"):
        return 0
    file_path = (hook_input.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0

    rel = rel_to_root(root, file_path)
    if rel is None:
        return 0

    session_id = hook_input.get("session_id")
    path = ledger_path(aware, session_id)
    led = load_ledger(path)

    aware_rel = rel_to_root(root, str(aware))
    claude_rel = "CLAUDE.md"
    if rel == claude_rel or (aware_rel and rel.startswith(aware_rel + "/")):
        if rel not in led["docs"]:
            led["docs"].append(rel)
    elif is_source(rel):
        if rel not in led["source"]:
            led["source"].append(rel)

    save_ledger(path, led)
    return 0


# ---------------------------------------------------------------------------
# sync  (Stop)
# ---------------------------------------------------------------------------

def drifted_subsystems(root, aware, led):
    """Subsystems with touched source this session whose doc wasn't edited."""
    table = parse_subsystem_table(root / "CLAUDE.md")
    if not table:
        return [], False

    edited_docs = set(led.get("docs", []))
    aware_rel = rel_to_root(root, str(aware)) or ".claude/awareness"
    edited_subsys_slugs = set()
    for d in edited_docs:
        m = re.match(re.escape(aware_rel) + r"/subsystems/(.+)\.md$", d)
        if m:
            edited_subsys_slugs.add(m.group(1))

    dirty = {}
    for rel in led.get("source", []):
        name = subsystem_for_file(rel, table)
        if name is None:
            continue
        dirty.setdefault(name, []).append(rel)

    drifted = []
    for name, files in dirty.items():
        doc = subsystem_doc(aware, name)
        if doc is None:
            continue  # can't nudge toward a doc that doesn't exist
        if slug(name) in edited_subsys_slugs:
            continue  # already updated this session
        drifted.append((name, slug(name), files))
    had_source = bool(led.get("source"))
    return drifted, had_source


def build_block_reason(aware_rel, drifted):
    lines = [
        "Awareness docs are out of date. You changed code in these "
        "subsystems this session but did not update their subsystem docs:",
        "",
    ]
    for name, sl, files in drifted:
        shown = ", ".join(files[:6]) + (" ..." if len(files) > 6 else "")
        lines.append(f"  - {name}  ->  {aware_rel}/subsystems/{sl}.md")
        lines.append(f"      changed: {shown}")
    lines += [
        "",
        "Update each listed subsystem doc to reflect what actually changed: "
        "public API (added/removed/renamed functions), new invariants or "
        "pitfalls you hit, and any cross-subsystem interactions. Keep it tight "
        "(50-150 lines per doc). Then stop.",
        "",
        "Do NOT regenerate the structural map by hand — that is maintained "
        "automatically. Only the prose (Layer 1/2) needs your judgment.",
    ]
    return "\n".join(lines)


def cmd_sync():
    hook_input = read_stdin_json()
    root = project_root(hook_input)
    aware = awareness_dir(root)
    if aware is None:
        return 0

    session_id = hook_input.get("session_id")
    path = ledger_path(aware, session_id)
    led = load_ledger(path)

    today = date.today().isoformat()
    claude_md = root / "CLAUDE.md"

    # (1) Layer 3: regenerate once per session if any source changed.
    if led.get("source") and not led.get("regen_done"):
        tokens = regenerate_structural_map(root, aware)
        led["regen_done"] = True
        if tokens is not None:
            stamp_structural_map(claude_md, today, tokens)
        save_ledger(path, led)

    # (2) Layer 1/2 drift enforcement.
    drifted, had_source = drifted_subsystems(root, aware, led)

    try:
        cap = int(os.environ.get("CCPROJECT_MAX_NUDGES") or DEFAULT_MAX_NUDGES)
    except ValueError:
        cap = DEFAULT_MAX_NUDGES

    enforce = os.environ.get("CCPROJECT_NO_ENFORCE") != "1"

    if enforce and drifted and (cap <= 0 or led.get("nudges", 0) < cap):
        led["nudges"] = led.get("nudges", 0) + 1
        save_ledger(path, led)
        aware_rel = rel_to_root(root, str(aware)) or ".claude/awareness"
        reason = build_block_reason(aware_rel, drifted)
        sys.stdout.write(json.dumps({
            "decision": "block",
            "reason": reason,
            "systemMessage": (
                f"ccproject — {len(drifted)} subsystem doc(s) drifted; "
                f"update before finishing (nudge #{led['nudges']})"
            ),
        }) + "\n")
        return 0

    # Clean (or cap reached / enforcement off): stamp and allow the stop.
    if had_source:
        stamp_last_updated(claude_md, today)
    return 0


# ---------------------------------------------------------------------------
# status  (SessionStart)
# ---------------------------------------------------------------------------

def newest_mtime(root, directory):
    base = (root / directory.strip("/")) if directory.strip("/") not in ("", ".") else root
    newest = 0.0
    if base.is_file():
        try:
            return base.stat().st_mtime
        except OSError:
            return 0.0
    if not base.is_dir():
        return 0.0
    for p in base.rglob("*"):
        if p.suffix.lower() in SOURCE_EXTS:
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    return newest


def cmd_status():
    hook_input = read_stdin_json()
    root = project_root(hook_input)
    aware = awareness_dir(root)
    if aware is None:
        return 0

    table = parse_subsystem_table(root / "CLAUDE.md")
    stale = []
    for name, directory in table:
        doc = subsystem_doc(aware, name)
        if doc is None:
            continue
        try:
            doc_mtime = doc.stat().st_mtime
        except OSError:
            continue
        if newest_mtime(root, directory) > doc_mtime:
            stale.append(name)

    if not stale:
        return 0

    msg = (
        "[ccproject] These subsystems have source newer than their awareness "
        "doc — consider refreshing before relying on them: "
        + ", ".join(sorted(stale))
        + ". Run /project:update-awareness, or just update the relevant "
        ".claude/awareness/subsystems/*.md as you work (the Stop hook will "
        "remind you if you touch their code without doing so)."
    )
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": msg,
        }
    }) + "\n")
    return 0


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "track": cmd_track,
    "sync": cmd_sync,
    "status": cmd_status,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: awareness_hooks.py {track|sync|status}\n")
        return 2
    if argv[0] in ("--version", "-V"):
        sys.stdout.write(f"awareness_hooks.py {VERSION}\n")
        return 0
    fn = COMMANDS.get(argv[0])
    if fn is None:
        sys.stderr.write(f"unknown subcommand: {argv[0]}\n")
        return 2
    try:
        return fn()
    except Exception:
        # Fail open: a hook must never wedge a session.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
