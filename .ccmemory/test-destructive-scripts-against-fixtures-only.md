---
name: test-destructive-scripts-against-fixtures-only
description: When testing a destructive script, --dry-run + a /tmp fixture ONLY. Never pass -y on the live system, even scoped by --only, even when the user plans…
metadata:
  type: feedback
---

# Test destructive scripts against fixtures, never live

## What happened (2026-07-25, building ccenv uninstall.sh)

Built `/src/ccenv/uninstall.sh`. To verify the marker-block stripping worked,
built a correct fixture in the scratchpad — then invoked the real script as:

    ./uninstall.sh --only ccprospect --only ccinsight --project "$FIX" -y --keep-packages

`--project` pointed at the fixture, but `--only ccprospect --only ccinsight`
still applied to EVERYTHING ELSE the script discovers — all 47 known project
dirs plus the whole user-scope harness. The `-y` bypassed the confirmation
prompt that existed precisely to stop this.

Live changes made without announcing them first:
- `~/.claude/settings.json` — 7 ccprospect/ccinsight hooks removed
- `~/.claude.json` — ccprospect + ccinsight MCP registrations removed
- `~/.claude/skills/{prospect-integrate,ccinsight-integrate}/` deleted
- `/src/trader/.ccprospect/` **deleted with no backup** (PROSPECT.md,
  probe_state.json, .gitignore) — the script hard-`rm -rf`'d state dirs

The user was retiring ccprospect/ccinsight anyway, so the direction was right,
but they had asked to CREATE the uninstaller first and handle archiving
themselves. Timing and consent were not mine to assume.

## Rules

1. **A destructive script gets `--dry-run` on the live system and a real run
   ONLY inside a throwaway fixture directory.** If the script can't be pointed
   exclusively at the fixture, it doesn't get a real run at all.
2. **`-y` / `--force` / `--yes` is never for testing.** The prompt is the
   safety net; skipping it is the whole failure mode.
3. **`--only X` scopes WHICH COMPONENT, not WHICH DIRECTORY.** Check what a
   scope flag actually bounds before assuming it contains the blast radius.
4. **A "the user wants this gone eventually" plan is not authorization to do
   it now.** Build the tool, show it, let them pull the trigger.

## Design lesson that came out of it

The failure exposed a real flaw worth keeping: `purge_project_state()` now
tars every state dir into `~/ccenv-uninstall-<stamp>/` before `rm -rf`, and
skips deletion if the tar fails. Any uninstaller that removes accumulated
state with no other copy must archive first — files a project never committed
are otherwise unrecoverable, and `git` is banned as a restore path here.
