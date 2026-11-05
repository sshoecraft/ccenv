---
name: compiled-ccenv-operating-lessons-assorted
description: Assorted ccenv lessons: hook gates, ccloop cutoff, installer seeds settings, no handoff docs, scripts not in /tmp, destructive-script testing, one-li…
metadata:
  type: feedback
tags: [compiled, ccenv, ccloop, install, hooks, assorted]
---

Assorted group (no single topic): seven ccenv/ccloop lessons the clusterer could not fit together. Common thread: things the user corrected or measured.

## Hooks and enforcement
Source: [[enforcement-hooks-that-check-file-written]]
- ccenv v0.33.0 deleted all three ccproject awareness hooks (`track` PostToolUse, `sync` Stop, `status` SessionStart) and `awareness_hooks.py`.
- Failure 1, per-session state files with no cleanup path: `track` wrote `.claude/awareness/.state/touched-<session-id>.json` on every Edit/Write; nothing deleted them; 810 accumulated in /src/mxfs. Nothing read them after session end (cross-process scratch, since each hook invocation is a fresh process), unbounded lifetime by omission. 141 KB of JSON occupied 3.3 MB on disk (810 files of ~170 bytes each burn a 4 KB block); `du -sh` vs `du -sb --apparent-size` disagree 23x, quote both. Not gitignored: 810 session IDs as commit candidates where the instruction is to stage everything.
- Failure 2, enforcement measuring the wrong thing: `sync` blocked session end until a subsystem doc was edited; it could check only WHETHER the file was written, so a no-op write satisfied it, and it demanded prose when a session is least able to write. Measured over 810 sessions: nudges=0 621; nudges=1 118; nudges=2 39; nudges=3 (cap) 32. The 32 were blocked three times, refused three times, let through with no doc update; ~96 re-fed turns for nothing. A gate whose predicate is "artifact was touched" is a formality with a cost; a check that cannot distinguish compliance from a no-op gets the no-op.
- Failure 3, mtime is not a drift signal: `status` compared source mtime to doc mtime and injected it at SessionStart. Checkout, branch switch or reformat bumps every mtime; /src is NFS-shared, muddying mtimes across machines. Its consumer was the model, never the user, who never read it.
- Migration: removing a hook from an installer is not enough; boxes that ran the old installer still have it in `~/.claude/settings.json`. Step 5 of ccproject's installer became UNREGISTER (otherwise every Edit/Write spawns python3 at a deleted script): strip only hooks whose command names the script, drop an entry whole when it was the only hook (no empty matcher stubs), delete the stale installed script. Test against a copy of a REAL settings.json (it had nine unrelated ccloop/ccmemory hooks on the same events): three removed, nine untouched.

## ccloop cutoff: never propose lowering it
Source: [[ccloop-cutoff-lowering-already-tried-and-lost]]
- User's ~2-week experiment, reverted 2026-08: `--cutoff=500` -> `--cutoff=145` -> back to 500. 145 lost despite keeping sessions under the 150k "expensive" threshold. A session reaches ~70k tokens before real work, a fixed cost rebuilt on every restart: cutoff 145k -> ~75k work/session, ~93% overhead; cutoff 500k -> ~430k work, ~16% overhead. User: "we burned so many tokens in that short period that it made it not worth it... I set it back to 500 and yes it costs more at the top end, but we don't end up with the constant restart churn."
- Measured 2026-08-22 with `scripts/startup_context_audit.py` over 409 mxfs sessions (S = first assistant turn's total prompt tokens, exact): CLI 2.1.239 S = 65,075 median. S grows with the CLI: 40,799 on 2.1.217 -> 65,075 on 2.1.239, +24k (+59%) in ~22 releases, ~+1.3k/release. Attribution: ~47k CLI floor, ~8.8k project CLAUDE.md, ~4.0k ccloop session prompt, ~2.2k global CLAUDE.md, ~3k agents/ccmemory. MCP tool schemas already deferred (`ENABLE_TOOL_SEARCH`; every sampled 2.1.239 session used ToolSearch). ~75% of S is not ours; perfect work on ours saves ~8k of 65k.
- Standing rules: (1) do not propose lowering the cutoff, not even "until S is reduced". (2) The only remaining lever is delta, context growth per request: keep grind output out of the parent via subagents. (3) Trimming the 35 KB mxfs CLAUDE.md is housekeeping (~4k/session for a 50% cut). Full analysis: `/src/ccenv/docs/context-economics.md`.

## Installer seeds settings, never overwrites
Source: [[installer-seeds-settings-never-overwrites]]
- ccenv v0.22.0 added `install_ccenv_settings()` to `install.sh` (step `settings`, right after base CLAUDE.md assembly), writing two keys to `~/.claude/settings.json`: `env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK = "1"` (kills the refusal fallback that silently re-runs a turn on a different model) and `switchModelsOnFlag = false` (belt and braces; a dialog instead of a silent swap).
- Correction: first cut enforced both values and rewrote a differing key. User, mid-implementation: "now make sure you dont set it if its already there." Rule: seed a MISSING key; a present key is the user's choice, left whatever the value, printing `already set, left alone: <key>=<value>`. When nothing was added, do not rewrite the file (mtime is a signal).
- Generalizes to any ccenv install path touching user config (`settings.json`, shell env files, `.claude.json`): add-if-missing, never enforce. Exception: wiring ccenv owns end to end (own hook entries, own MCP registrations), where healing a stale path is the point.
- Tests: `tests/test_settings_step.sh` lifts the function by name (`sed -n '/^install_ccenv_settings() {$/,/^}$/p'` + `eval`), runs against throwaway /tmp HOME fixtures; 19 assertions incl. present-value-kept and file-not-rewritten.

## Never make sessions maintain handoff docs
Source: [[never-make-sessions-maintain-handoff-docs]]
- User, blunt and repeated (2026-08-14): the handoff-document instruction "has cost me millions in tokens". Banned; supersedes the 0.20.0 decision in `handoff-docs-must-be-freshness-stamped`. ccenv v0.21.0 removed it from ccloop.
- Do not put "keep a handoff/state/summary file current" in any prompt, preamble, skill or hook: not as a tier, not with a freshness check, not "just a short one". It charges output tokens every turn to reproduce, worse, what the harness writes free.
- Instead: Claude Code writes `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. Locate the previous session deterministically and hand over the path. User's own handoff: "read previous claude code session for context". ccloop `runner.prior_session_transcript`, two tiers: (1) within a run, `sessions.log` holds session-ids in order; the last with an existing transcript is the predecessor, walking backwards so a deleted one falls through; (2) first session of a run: newest non-empty `.jsonl` in the project dir, excluding this run's ids. Nothing to point at -> emit nothing; never name a nonexistent path.
- A transcript is multi-megabyte JSONL; the pointer must ship size, line count, a suggested `Read` offset near the tail (`lines - 300`), and a `grep` example.
- Why it wins: free; cannot go stale (no mtime checks, STALE markers, scraped fallbacks); complete (tool results and reasoning). Non-Anthropic API backends driving `claude` cannot be trusted to know their transcript path, so the wrapper computes and states it.

## Scripts never go in /tmp
Source: [[scripts-never-go-in-tmp]]
- The v0.13.3 wording ("might be used again" -> `tests/`, truly temporary -> /tmp) failed: predicting reruns was reliably wrong, scripts landed in /tmp and were wiped on reboot. v0.18.1 splits by kind: scripts are categorically not temp files (harness, probe, repro, one-off migration, data-munger, helper): harnesses/probes/repros -> `tests/`; utilities/ops/build helpers -> `tools/` or `scripts/` (create `scripts/` if neither). /tmp is for data only: scratch output, blobs, throwaway fixtures, dumps.
- Standing tension: the session harness scratchpad (`/tmp/claude-<uid>/.../scratchpad`) says all temp files go there; CLAUDE.md overrides for anything executable. Compatible with [[test-destructive-scripts-against-fixtures-only]]: the fixture tree is data and stays in /tmp, the script operating on it does not.
- Landed in `/src/ccenv/CLAUDE.md` (verbatim `[CCENV MANAGED]` source) and mirrored byte-identically into `~/.claude/CLAUDE.md`; VERSION 0.18.0 -> 0.18.1, CHANGELOG + `docs/install.md` History. See `repo-claude-md-is-managed-block-source`.

## Test destructive scripts against fixtures only
Source: [[test-destructive-scripts-against-fixtures-only]]
- 2026-07-25, building `/src/ccenv/uninstall.sh`: after building a correct fixture, ran `./uninstall.sh --only ccprospect --only ccinsight --project "$FIX" -y --keep-packages`. `--project` pointed at the fixture but `--only` applied to everything else discovered (all 47 known project dirs plus user-scope harness), and `-y` bypassed the prompt. Live changes unannounced: `~/.claude/settings.json` 7 ccprospect/ccinsight hooks removed; `~/.claude.json` their MCP registrations removed; `~/.claude/skills/{prospect-integrate,ccinsight-integrate}/` deleted; `/src/trader/.ccprospect/` deleted with no backup (PROSPECT.md, probe_state.json, .gitignore) by hard `rm -rf`. The user was retiring them anyway but had asked only to CREATE the uninstaller and handle archiving themselves.
- Rules: (1) destructive script: `--dry-run` on live, real run ONLY in a throwaway fixture; if it cannot be pointed exclusively at the fixture, no real run. (2) `-y`/`--force`/`--yes` never for testing. (3) `--only X` scopes COMPONENT, not DIRECTORY; check what a scope flag bounds. (4) "User wants it gone eventually" is not authorization now.
- Design lesson: `purge_project_state()` now tars every state dir into `~/ccenv-uninstall-<stamp>/` before `rm -rf`, skipping deletion if tar fails. Uninstallers removing state with no other copy must archive first; git is banned as a restore path.

## One-line copy-paste commands
Source: [[one-line-copy-paste-commands]]
- Copy-paste commands in install.sh output (or any user-facing instruction) MUST be one physical line, no `\` continuations. User: "THIS _MUST_ be 1 line!!! MUST".
- Bad: `grep -q PYTHONUSERBASE $HOME/.zshrc 2>/dev/null || \` newline `echo 'export PYTHONUSERBASE="$HOME/.local"' >> $HOME/.zshrc`. Good: the same on one line.
- Why: continuations break under terminal wrap, partial selection, some SSH paste paths, or being read aloud. Apply: in the REQUIRED-setup banner and similar output, one `echo` per command, no `\` splits, no multi-line heredocs to retype.
