#!/bin/bash
# Project Awareness System — Installer
# Installs the skill, scripts, global CLAUDE.md snippet, and templates
# Works on any system with Claude Code installed

set -e

SKILL_DIR="$HOME/.claude/skills/project-awareness"
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Project Awareness System Installer ==="
echo ""

# Step 1: Check prerequisites
echo "[1/6] Checking prerequisites ..."
if ! command -v python3 &>/dev/null; then
    echo "  ERROR: Python 3 is required but not found."
    echo "  Install Python 3 and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print('{}.{}'.format(sys.version_info.major, sys.version_info.minor))")
echo "  Python 3 found: $PYTHON_VERSION"

# Check for tree-sitter (optional)
if python3 -c "import tree_sitter" 2>/dev/null; then
    echo "  tree-sitter: installed (accurate AST parsing enabled)"
    if python3 -c "import tree_sitter_c" 2>/dev/null; then
        echo "    tree-sitter-c: installed"
    else
        echo "    tree-sitter-c: not installed (optional: pip install tree-sitter-c)"
    fi
    if python3 -c "import tree_sitter_cpp" 2>/dev/null; then
        echo "    tree-sitter-cpp: installed"
    else
        echo "    tree-sitter-cpp: not installed (optional: pip install tree-sitter-cpp)"
    fi
else
    echo "  tree-sitter: not installed (using regex fallback — still works)"
    echo "    For better accuracy: pip install tree-sitter tree-sitter-c tree-sitter-cpp"
fi

# Step 2: Install the skill and scripts
echo ""
echo "[2/6] Installing skill to $SKILL_DIR ..."
mkdir -p "$SKILL_DIR/templates/commands"
mkdir -p "$SKILL_DIR/scripts"
cp "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR/SKILL.md"
cp "$SCRIPT_DIR/templates/CLAUDE.md.template" "$SKILL_DIR/templates/"
cp "$SCRIPT_DIR/templates/subsystem.md.template" "$SKILL_DIR/templates/"
cp "$SCRIPT_DIR/templates/structural-map.md.template" "$SKILL_DIR/templates/"
cp "$SCRIPT_DIR/templates/commands/bootstrap-awareness.md" "$SKILL_DIR/templates/commands/"
cp "$SCRIPT_DIR/templates/commands/refresh-map.md" "$SKILL_DIR/templates/commands/"
cp "$SCRIPT_DIR/templates/commands/update-awareness.md" "$SKILL_DIR/templates/commands/"
echo "  Done."

# Step 3: Install analysis scripts
echo "[3/6] Installing analysis scripts ..."
cp "$SCRIPT_DIR/scripts/analyze_project.py" "$SKILL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/generate_structural_map.py" "$SKILL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/generate_mermaid_callgraph.py" "$SKILL_DIR/scripts/"
cp "$SCRIPT_DIR/scripts/awareness_hooks.py" "$SKILL_DIR/scripts/"
chmod +x "$SKILL_DIR/scripts/analyze_project.py"
chmod +x "$SKILL_DIR/scripts/generate_structural_map.py"
chmod +x "$SKILL_DIR/scripts/generate_mermaid_callgraph.py"
chmod +x "$SKILL_DIR/scripts/awareness_hooks.py"
echo "  Installed 4 scripts to $SKILL_DIR/scripts/"

# Step 4: Update global awareness protocol in ~/.claude/CLAUDE.md
echo "[4/6] Updating global CLAUDE.md ..."
if [ "${CCPROJECT_SKIP_GLOBAL_CLAUDE_MD:-0}" = "1" ]; then
    echo "  CCPROJECT_SKIP_GLOBAL_CLAUDE_MD=1 — skipping (top-level installer owns this)."
else
mkdir -p "$HOME/.claude"
SNIPPET_FILE="$SCRIPT_DIR/global-claude-md-snippet.md"
if [ -f "$GLOBAL_CLAUDE_MD" ]; then
    if grep -q "\[AWARENESS PROTOCOL\]" "$GLOBAL_CLAUDE_MD" 2>/dev/null; then
        # Extract existing section (from marker to EOF) and compare to snippet
        EXISTING=$(sed -n '/^# \[AWARENESS PROTOCOL\]/,$p' "$GLOBAL_CLAUDE_MD")
        NEW=$(cat "$SNIPPET_FILE")
        if [ "$EXISTING" = "$NEW" ]; then
            echo "  Global awareness protocol is up to date — no changes needed."
        else
            # Remove old section and append updated snippet
            # Use a temp file to avoid clobbering
            TMPFILE=$(mktemp)
            sed '/^# \[AWARENESS PROTOCOL\]/,$d' "$GLOBAL_CLAUDE_MD" > "$TMPFILE"
            # Remove trailing blank lines from the truncated file
            sed -i.bak -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$TMPFILE" 2>/dev/null || true
            rm -f "$TMPFILE.bak"
            echo "" >> "$TMPFILE"
            cat "$SNIPPET_FILE" >> "$TMPFILE"
            mv "$TMPFILE" "$GLOBAL_CLAUDE_MD"
            echo "  Updated awareness protocol in $GLOBAL_CLAUDE_MD (content changed)."
        fi
    else
        echo "" >> "$GLOBAL_CLAUDE_MD"
        cat "$SNIPPET_FILE" >> "$GLOBAL_CLAUDE_MD"
        echo "  Appended awareness protocol to existing $GLOBAL_CLAUDE_MD"
    fi
else
    cp "$SNIPPET_FILE" "$GLOBAL_CLAUDE_MD"
    echo "  Created $GLOBAL_CLAUDE_MD with awareness protocol."
fi
fi  # end of CCPROJECT_SKIP_GLOBAL_CLAUDE_MD gate

# Step 5: Register awareness-maintenance hooks in ~/.claude/settings.json
echo "[5/6] Registering awareness hooks ..."
HOOK_SCRIPT="$SKILL_DIR/scripts/awareness_hooks.py"
PYTHON_BIN="$(command -v python3)"
SETTINGS_JSON="$HOME/.claude/settings.json" \
HOOK_CMD_PREFIX="$PYTHON_BIN $HOOK_SCRIPT" \
python3 - <<'PY'
import json, os
from pathlib import Path

settings_path = Path(os.environ["SETTINGS_JSON"])
settings_path.parent.mkdir(parents=True, exist_ok=True)
prefix = os.environ["HOOK_CMD_PREFIX"]  # "python3 /abs/awareness_hooks.py"

# event -> (matcher or None, subcommand)
WANT = [
    ("PostToolUse", "Edit|Write|MultiEdit", "track"),
    ("Stop",        None,                    "sync"),
    ("SessionStart", None,                   "status"),
]

if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text() or "{}")
    except json.JSONDecodeError:
        data = {}
else:
    data = {}
if not isinstance(data, dict):
    data = {}

hooks = data.setdefault("hooks", {})

def is_ours(cmd):
    return bool(cmd) and "awareness_hooks.py" in cmd

changed = False
for event, matcher, sub in WANT:
    command = f"{prefix} {sub}"
    entries = hooks.get(event) or []
    # Drop any stale entry of ours for this subcommand (self-heal a moved path),
    # preserving foreign hooks untouched.
    rebuilt = []
    present = False
    for entry in entries:
        kept = []
        for h in entry.get("hooks", []):
            c = h.get("command")
            if is_ours(c) and c.split()[-1] == sub:
                if c == command and entry.get("matcher") == matcher:
                    present = True
                    kept.append(h)
                else:
                    changed = True  # stale path/matcher — drop it
            else:
                kept.append(h)
        if kept:
            e = dict(entry)
            e["hooks"] = kept
            rebuilt.append(e)
    if not present:
        new_entry = {"hooks": [{"type": "command", "command": command}]}
        if matcher is not None:
            new_entry["matcher"] = matcher
        rebuilt.append(new_entry)
        changed = True
    hooks[event] = rebuilt

if changed:
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print("  registered/updated ccproject hooks (PostToolUse/Stop/SessionStart)")
else:
    print("  ccproject hooks already registered")
PY

# Step 6: Verify
echo "[6/6] Verifying installation ..."
ERRORS=0
[ -f "$SKILL_DIR/SKILL.md" ] || { echo "  ERROR: SKILL.md not found"; ERRORS=1; }
[ -f "$SKILL_DIR/templates/CLAUDE.md.template" ] || { echo "  ERROR: CLAUDE.md.template not found"; ERRORS=1; }
[ -f "$SKILL_DIR/templates/subsystem.md.template" ] || { echo "  ERROR: subsystem.md.template not found"; ERRORS=1; }
[ -f "$SKILL_DIR/templates/structural-map.md.template" ] || { echo "  ERROR: structural-map.md.template not found"; ERRORS=1; }
[ -f "$SKILL_DIR/scripts/analyze_project.py" ] || { echo "  ERROR: analyze_project.py not found"; ERRORS=1; }
[ -f "$SKILL_DIR/scripts/generate_structural_map.py" ] || { echo "  ERROR: generate_structural_map.py not found"; ERRORS=1; }
[ -f "$SKILL_DIR/scripts/generate_mermaid_callgraph.py" ] || { echo "  ERROR: generate_mermaid_callgraph.py not found"; ERRORS=1; }
[ -f "$SKILL_DIR/scripts/awareness_hooks.py" ] || { echo "  ERROR: awareness_hooks.py not found"; ERRORS=1; }
[ -x "$SKILL_DIR/scripts/analyze_project.py" ] || { echo "  ERROR: analyze_project.py not executable"; ERRORS=1; }
grep -q "\[AWARENESS PROTOCOL\]" "$GLOBAL_CLAUDE_MD" || { echo "  ERROR: Global CLAUDE.md missing awareness protocol"; ERRORS=1; }

if [ $ERRORS -eq 0 ]; then
    echo "  All checks passed."
    echo ""
    echo "=== Installation complete ==="
    echo ""
    echo "Installed to:"
    echo "  Skill:      $SKILL_DIR/SKILL.md"
    echo "  Scripts:    $SKILL_DIR/scripts/"
    echo "  Global MD:  $GLOBAL_CLAUDE_MD"
    echo "  Templates:  $SKILL_DIR/templates/"
    echo ""
    echo "Scripts installed:"
    echo "  analyze_project.py          — Auto-detect languages, subsystems, dependencies"
    echo "  generate_structural_map.py  — Extract signatures, types, call graph"
    echo "  generate_mermaid_callgraph.py — Visual call graph in Mermaid format"
    echo "  awareness_hooks.py          — Auto-maintain docs (track/sync/status hooks)"
    echo ""
    echo "Awareness hooks registered in ~/.claude/settings.json (self-gate on"
    echo "projects that have .claude/awareness/ — no-ops everywhere else):"
    echo "  PostToolUse  -> track   record touched source/doc files per session"
    echo "  Stop         -> sync    auto-regen structural map; block on doc drift"
    echo "  SessionStart -> status  report subsystems whose source outran their doc"
    echo ""
    echo "Usage:"
    echo "  1. Open Claude Code in any project directory"
    echo "  2. Say: 'Bootstrap awareness for this project'"
    echo "  3. Claude will run the analysis scripts and generate the three-layer infrastructure"
    echo ""
    echo "After bootstrap, every new session automatically loads the 30,000-foot view"
    echo "and knows how to find subsystem details on demand."
else
    echo ""
    echo "=== Installation had errors — check above ==="
    exit 1
fi
