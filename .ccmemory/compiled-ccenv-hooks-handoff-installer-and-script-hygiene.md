---
name: compiled-ccenv-hooks-handoff-installer-and-script-hygiene
description: Assorted ccenv feedback: no-op-satisfiable hooks, no cutoff lowering, installer seeds settings, no handoff docs, scripts not in /tmp, destructive-tes…
metadata:
  type: feedback
tags: [compiled, ccenv, hooks, ccloop, installer, feedback]
---

Assorted group: unrelated ccenv/ccloop corrections with no single shared topic; organised by area.

## Hook design: ccproject awareness hooks removed (`enforcement-hooks-that-check-file-written`)
ccenv v0.33.0 deleted all three ccproject awareness hooks (`track` PostToolUse, `sync` Stop, `status` SessionStart) and `awareness_hooks.py`.
1. **Per-session state files with no cleanup path.** `track` wrote `.claude/awareness/.state/touched-<session-id>.json` on every Edit/Write; nothing deleted them. 810 accumulated in /src/mxfs. Nothing read them after the session (cross-process scratch memory, since each hook invocation is a fresh process), unbounded lifetime by omission. 141 KB of JSON occupied 3.3 MB of disk (810 files of ~170 bytes each burn a 4 KB block); `du -sh` and `du -sb --apparent-size` disagree by 23x, quote both. Also not gitignored: 810 session IDs as commit candidates where the instruction is to stage everything.
2. **Enforcement that measures the wrong thing.** `sync` blocked session end until a subsystem doc was edited; it could only check WHETHER the file was written, so a no-op write satisfied it, and it demanded prose when a session is least able to write. Measured over 810 sessions: nudges=0 621; nudges=1 118; nudges=2 39; nudges=3 (cap) 32. The 32 were blocked three times, refused three times, then let through with no doc update; ~96 re-fed turns for nothing. Generalize: a gate whose predicate is "artifact was touched" is a formality with a cost; a check that cannot distinguish compliance from a no-op gets the no-op.
3. **mtime is not a drift signal.** `status` compared source mtime to doc mtime and injected it at SessionStart. Checkout, branch switch or reformat bumps every mtime; /src is NFS-shared, muddling mtimes across machines. Its consumer was the model, never displayed to the user.
- Migration: removing a hook from an installer is not enough; boxes that ran the old installer still have it in `~/.claude/settings.json`. Step 5 of ccproject's installer became UNREGISTER (else every Edit/Write spawns python3 at a deleted script): strip only hooks whose command names the script, drop an entry whole when it was its only hook (no empty matcher stubs), delete the stale installed script. Test against a copy of a REAL settings.json: it had nine unrelated ccloop/ccmemory hooks on the same events; three removed, nine untouched.

## ccloop --cutoff: never propose lowering (`ccloop-cutoff-lowering-already-tried-and-lost`)
- User ran `--cutoff=500` -> `145` -> back to `500` over ~2 weeks (reverted 2026-08). 145 lost despite staying under the 150k "expensive" threshold. A session reaches ~70k context before real work; fixed cost rebuilt on every restart: cutoff 145k = ~75k work/session, ~93% overhead; 500k = ~430k work, ~16% overhead. User: "we burned so many tokens in that short period that it made it not worth it... I set it back to 500 and yes it costs more at the top end, but we don't end up with the constant restart churn."
- Measured 2026-08-22 with `scripts/startup_context_audit.py` over 409 mxfs sessions (S = first assistant turn's total prompt tokens, exact): CLI 2.1.239 median S = 65,075 (confirms ~70k). S grows with the CLI: 40,799 on 2.1.217 -> 65,075 on 2.1.239, +24k (+59%) in ~22 releases, ~+1.3k/release. Attribution: ~47k CLI floor, ~8.8k project CLAUDE.md, ~4.0k ccloop session prompt, ~2.2k global CLAUDE.md, ~3k agents/ccmemory. MCP schemas already deferred (`ENABLE_TOOL_SEARCH`). ~75% of S is not ours; perfect work saves ~8k of 65k.
- Standing rules: (1) do not propose lowering cutoff, permanently; (2) the remaining lever is delta, context growth per request: keep grind output out of the parent via subagents; (3) trimming the 35 KB mxfs CLAUDE.md is housekeeping (~4k/session for a 50% cut). Analysis: `/src/ccenv/docs/context-economics.md`.

## Installer seeds settings, never overwrites (`installer-seeds-settings-never-overwrites`)
- ccenv v0.22.0 added `install_ccenv_settings()` (step `settings`, right after base CLAUDE.md assembly) writing `env.CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK = "1"` (kills silent re-run on a different model) and `switchModelsOnFlag = false` (belt and braces; costs a dialog instead of a silent swap).
- First cut enforced both values and rewrote a differing key. User: "now make sure you dont set it if its already there."
- Rule: seed a MISSING key; a present key is the user's choice whatever the value, print `already set, left alone: <key>=<value>`. If nothing added, do not rewrite the file (mtime is a signal). Applies to any install path touching user config (`settings.json`, shell env files, `.claude.json`). Exception: wiring ccenv owns end to end (own hook entries, own MCP registrations) where healing a stale path is the point.
- Test: `tests/test_settings_step.sh` lifts the function out of `install.sh` (`sed -n '/^install_ccenv_settings() {$/,/^}$/p'` + `eval`) and runs it against throwaway /tmp HOME fixtures; 19 assertions incl. present-value-kept and file-not-rewritten.

## No handoff docs (`never-make-sessions-maintain-handoff-docs`)
- User, blunt and repeated (2026-08-14): the handoff-doc instruction "has cost me millions in tokens". Banned; supersedes the 0.20.0 decision in `handoff-docs-must-be-freshness-stamped`. ccenv v0.21.0 removed it from ccloop.
- Never put "keep a handoff/state/summary file current" in any prompt, preamble, skill or hook, not as a tier, not with freshness checks.
- Instead point at the transcript `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`. The user's own handoff is one line: "read previous claude code session for context". ccloop `runner.prior_session_transcript`, two tiers: (1) within a run, `sessions.log` holds session-ids in order; last one whose transcript exists is the predecessor, walk backwards so a deleted transcript falls through; (2) first session of a run: newest non-empty `.jsonl` in the cwd-slug dir, excluding this run's ids. Nothing to point at -> emit nothing; never name a nonexistent path.
- A transcript is multi-MB JSONL; the pointer must ship with size, line count, suggested `Read` offset near tail (`lines - 300`), and a `grep` example.
- Wins: free, cannot go stale (no mtime checks/STALE markers/scraped fallback; the 0.20.0 machinery was overhead), complete. Non-Anthropic API backends can't be relied on to know their transcript path, so the wrapper computes and states it.

## Scripts never in /tmp (`scripts-never-go-in-tmp`)
- v0.13.3 wording ("might be used again -> tests/, truly temporary -> /tmp") failed: predicting reuse was reliably wrong, every script came with a throwaway story and got wiped on reboot. v0.18.1 splits by KIND: scripts (harness, probe, repro, one-off migration, data-munger, helper) always go in the repo; harnesses/probes/repros -> `tests/`; utilities/ops/build helpers -> `tools/` or `scripts/` (create `scripts/` if neither). /tmp is data only (scratch output, blobs, throwaway fixtures, dumps).
- Tension: the Claude Code harness scratchpad `/tmp/claude-<uid>/.../scratchpad` says all temp files go there; CLAUDE.md overrides for anything executable.
- Compatible with `test-destructive-scripts-against-fixtures-only`: the fixture tree is data, stays in /tmp; the script operating on it does not.
- Landed in `/src/ccenv/CLAUDE.md` (verbatim `[CCENV MANAGED]` source), mirrored byte-identically into `~/.claude/CLAUDE.md`; VERSION 0.18.0->0.18.1, CHANGELOG + `docs/install.md` History. See `repo-claude-md-is-managed-block-source`.

## Destructive scripts: fixtures only (`test-destructive-scripts-against-fixtures-only`)
- 2026-07-25, building `/src/ccenv/uninstall.sh`: built a fixture, then ran `./uninstall.sh --only ccprospect --only ccinsight --project "$FIX" -y --keep-packages`. `--project` pointed at the fixture but `--only` still applied to everything discovered: all 47 known project dirs plus user-scope harness; `-y` bypassed the confirmation prompt.
- Live changes: `~/.claude/settings.json` 7 ccprospect/ccinsight hooks removed; `~/.claude.json` both MCP registrations removed; `~/.claude/skills/{prospect-integrate,ccinsight-integrate}/` deleted; `/src/trader/.ccprospect/` (PROSPECT.md, probe_state.json, .gitignore) deleted with no backup (hard `rm -rf`). User was retiring them anyway but had asked only to CREATE the uninstaller.
- Rules: (1) destructive script gets `--dry-run` live and a real run ONLY in a throwaway fixture; if it can't be pointed exclusively at the fixture, no real run. (2) `-y`/`--force`/`--yes` never for testing. (3) `--only X` scopes component, not directory; check what a scope flag bounds. (4) "user wants it gone eventually" is not authorization now.
- Design fix: `purge_project_state()` now tars every state dir into `~/ccenv-uninstall-<stamp>/` before `rm -rf` and skips deletion if tar fails. Any uninstaller removing state with no other copy must archive first; `git` is banned as a restore path.

## One-line copy-paste commands (`one-line-copy-paste-commands`)
- Copy-paste commands in install.sh output (or any user instruction) must be ONE physical line, no `\` continuations. User: "THIS _MUST_ be 1 line!!! MUST".
- Bad: `grep -q PYTHONUSERBASE $HOME/.zshrc 2>/dev/null || \` newline `echo 'export PYTHONUSERBASE="$HOME/.local"' >> $HOME/.zshrc`. Good: same on one line.
- Why: continuations break under terminal wrap, partial selection, some SSH paste, reading aloud. Apply: in install.sh's REQUIRED-setup banner, one `echo` per command; no multi-line heredocs the user must retype.
