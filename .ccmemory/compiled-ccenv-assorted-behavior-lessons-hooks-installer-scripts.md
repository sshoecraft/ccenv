---
name: compiled-ccenv-assorted-behavior-lessons-hooks-installer-scripts
description: Assorted ccenv lessons: no-op-satisfiable hooks, ccloop cutoff, installer seeds settings, no handoff docs, scripts not in /tmp, fixture-only destruct…
metadata:
  type: feedback
tags: [compiled, ccenv, ccloop, hooks, installer, feedback]
---

Assorted group: the notes share no single topic; all are user corrections or measured lessons on ccenv hooks, ccloop, the installer and script handling.

## Hooks that check "file written" are formalities

[[enforcement-hooks-that-check-file-written]]: ccenv v0.33.0 deleted all three ccproject awareness hooks (`track` PostToolUse, `sync` Stop, `status` SessionStart) and `awareness_hooks.py`.
1. Per-session state with no cleanup: `track` wrote `.claude/awareness/.state/touched-<session-id>.json` on every Edit/Write; nothing deleted them; 810 accumulated in /src/mxfs and nothing read them after the session (cross-process scratch, unbounded lifetime by omission). 141 KB of JSON took 3.3 MB of disk (810 files of ~170 bytes each burn a 4 KB block); `du -sh` vs `du -sb --apparent-size` differ 23x, quote both. Not gitignored: 810 session IDs as commit candidates where the instruction is to stage everything.
2. Wrong measure: `sync` blocked session end until a subsystem doc was edited; it could check only WHETHER the file was written, so a no-op write satisfied it, and it demanded prose when a session is least able to write. Record over 810 sessions: nudges=0 621, nudges=1 118, nudges=2 39, nudges=3 (cap) 32. The 32 were blocked three times, refused three times, then let through with no doc update; ~96 re-fed turns for nothing. A gate whose predicate is "the artifact was touched" is a formality with a cost; a check that cannot distinguish compliance from a no-op gets the no-op.
3. mtime is not a drift signal: `status` compared source mtime to doc mtime and injected it at SessionStart; a checkout, branch switch or reformat bumps every mtime, /src is NFS-shared which muddles mtimes across machines. Its consumer was the model, never displayed to the user (who correctly never read it).
- Migration: removing a hook from an installer is not enough; boxes that ran the old one still register it in `~/.claude/settings.json`. ccproject installer step 5 became UNREGISTER (else every Edit/Write spawns python3 at a deleted script): strip only hooks whose command names the script, drop an entry whole when it was its only hook (no empty matcher stubs), delete the stale installed script. Test against a copy of a REAL settings.json: it had nine unrelated ccloop/ccmemory hooks on the same events; three removed, nine untouched.

## ccloop --cutoff: never propose lowering it

[[ccloop-cutoff-lowering-already-tried-and-lost]]
- User experiment (~2 weeks, reverted 2026-08): `--cutoff=500` -> `145` -> back to `500`. 145 lost though it kept sessions under the 150k "expensive" threshold. A session reaches ~70k context before real work, rebuilt on every restart: cutoff 145k = ~75k real work, ~93% overhead; 500k = ~430k real work, ~16% overhead. User: "we burned so many tokens in that short period that it made it not worth it... I set it back to 500 and yes it costs more at the top end, but we don't end up with the constant restart churn."
- Measured 2026-08-22, `scripts/startup_context_audit.py` over 409 mxfs sessions (S = first assistant turn's total prompt tokens, exact): CLI 2.1.239 S = 65,075 median. S grew from 40,799 on 2.1.217: +24k (+59%) in ~22 releases, ~+1.3k per release. Attribution: ~47k CLI floor, ~8.8k project CLAUDE.md, ~4.0k ccloop session prompt, ~2.2k global CLAUDE.md, ~3k agents/ccmemory. MCP schemas already deferred (`ENABLE_TOOL_SEARCH`; every sampled 2.1.239 session used ToolSearch). ~75% of S is not ours; perfect work on our share saves ~8k of 65k.
- Standing rules: (1) do not propose lowering the cutoff, permanently; (2) the only remaining lever is delta, context growth per request: keep grind output out of the parent via subagents; (3) trimming the 35 KB mxfs CLAUDE.md is housekeeping (~4k/session for 50% cut). Full analysis: `/src/ccenv/docs/context-economics.md`.

## Installer seeds settings, never overwrites

[[installer-seeds-settings-never-overwrites]]
- ccenv v0.22.0 added `install_ccenv_settings()` (step `settings`, right after base CLAUDE.md assembly) writing `env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK = "1"` (kills silent re-run on a different model) and `switchModelsOnFlag = false` (belt and braces; costs a dialog instead of silent swap) into `~/.claude/settings.json`.
- First cut enforced both and rewrote a differing key. User: "now make sure you dont set it if its already there." Rule: seed a MISSING key; a present key is the user's choice whatever the value: print `already set, left alone: <key>=<value>`. If nothing added, do not rewrite the file (mtime is a signal). Generalizes to any install path touching user config (`settings.json`, shell env files, `.claude.json`): add-if-missing, never enforce. Exception: wiring ccenv owns end to end (own hook entries, MCP registrations), where healing a stale path is the point.
- Test `tests/test_settings_step.sh` lifts the function by name (`sed -n '/^install_ccenv_settings() {$/,/^}$/p'` + `eval`) and runs it against throwaway /tmp HOME fixtures; 19 assertions incl. present-value-kept and file-not-rewritten.

## Never make sessions maintain handoff docs

[[never-make-sessions-maintain-handoff-docs]]
- User 2026-08-14: the handoff-doc instruction "has cost me millions in tokens"; banned; supersedes the 0.20.0 decision in `handoff-docs-must-be-freshness-stamped`. ccenv v0.21.0 removed it from ccloop. Never put "keep a handoff/state/summary file current" in any prompt, preamble, skill or hook, not as a tier, not with a freshness check.
- Instead: the harness writes `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. User's own handoff is one line: "read previous claude code session for context". ccloop `runner.prior_session_transcript`: (1) within a run, `sessions.log` session-ids in order, last whose transcript exists is the predecessor, walking backwards; (2) first session of a run: newest non-empty `.jsonl` in the project dir, excluding this run's ids. Nothing to point at -> emit nothing; never name a nonexistent path.
- Multi-megabyte JSONL: the pointer must ship size, line count, suggested `Read` offset near tail (`lines - 300`) and a `grep` example.
- Transcript wins: free, cannot go stale (no mtime checks/STALE markers/scraped fallback), complete incl. tool results. Non-Anthropic API backends can't be trusted to know their transcript path, so the wrapper computes and states it.

## Scripts never go in /tmp

[[scripts-never-go-in-tmp]] (v0.18.1)
- v0.13.3 wording ("might be used again" -> `tests/`, truly temporary -> /tmp) failed: predicting reuse was reliably wrong, scripts landed in /tmp and got wiped on reboot. Split by KIND: scripts (harness, probe, repro, migration, data-munger, helper) always in repo: harnesses/probes/repros -> `tests/`; utilities/ops/build helpers -> `tools/` or `scripts/` (create `scripts/` if neither). /tmp is data only (scratch output, blobs, throwaway fixtures, dumps).
- Tension: the Claude Code harness scratchpad `/tmp/claude-<uid>/.../scratchpad` says all temp files go there; CLAUDE.md overrides for anything executable. Compatible with [[test-destructive-scripts-against-fixtures-only]]: the fixture tree is data (in /tmp), the script operating on it is not.
- Landed in `/src/ccenv/CLAUDE.md` and mirrored byte-identically into `~/.claude/CLAUDE.md`; VERSION 0.18.0->0.18.1, CHANGELOG + `docs/install.md` History. See `repo-claude-md-is-managed-block-source`.

## Destructive scripts: dry-run and fixtures only

[[test-destructive-scripts-against-fixtures-only]] (2026-07-25, building `/src/ccenv/uninstall.sh`)
- Ran `./uninstall.sh --only ccprospect --only ccinsight --project "$FIX" -y --keep-packages`. `--project` pointed at the fixture but `--only` scoped components, not directories, so it hit all 47 known project dirs and the user-scope harness; `-y` bypassed the prompt. Live changes: `~/.claude/settings.json` 7 ccprospect/ccinsight hooks removed; `~/.claude.json` MCP registrations removed; `~/.claude/skills/{prospect-integrate,ccinsight-integrate}/` deleted; `/src/trader/.ccprospect/` deleted with no backup (PROSPECT.md, probe_state.json, .gitignore) via hard `rm -rf`. User was retiring them anyway but had asked only to CREATE the uninstaller and handle archiving themselves.
- Rules: (1) `--dry-run` on live, real run ONLY in a throwaway fixture; if the script can't be pointed exclusively at it, no real run. (2) `-y`/`--force`/`--yes` never for testing. (3) `--only X` scopes component not directory; check what a flag bounds. (4) "User wants this gone eventually" is not authorization now.
- Design fix: `purge_project_state()` tars every state dir into `~/ccenv-uninstall-<stamp>/` before `rm -rf`, skipping deletion if tar fails. Uninstallers removing state with no other copy must archive first; git is banned as a restore path.

## Copy-paste commands are one physical line

[[one-line-copy-paste-commands]]
- Copy-paste commands in install.sh output (or any user instruction) MUST be one physical line, no `\` continuations. User: "THIS _MUST_ be 1 line!!! MUST".
- Bad: `grep -q PYTHONUSERBASE $HOME/.zshrc 2>/dev/null || \` newline `echo 'export PYTHONUSERBASE="$HOME/.local"' >> $HOME/.zshrc`. Good: the same on one line.
- Why: terminal wrap, partial selection, SSH paste clients, reading aloud. Apply: in install.sh's REQUIRED-setup banner, one `echo` per command, no `\` splits, no multi-line heredocs to retype.
