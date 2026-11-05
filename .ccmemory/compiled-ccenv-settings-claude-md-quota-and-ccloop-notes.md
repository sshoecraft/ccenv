---
name: compiled-ccenv-settings-claude-md-quota-and-ccloop-notes
description: Assorted ccenv notes: commit attribution keys, agents embedding no rules, quota buckets, handoff doc reversal, managed CLAUDE.md block, ccloop flag t…
metadata:
  type: project
tags: [compiled, ccenv, ccloop, settings, quota, claude-md, assorted]
---

Assorted (no single topic): six ccenv notes about harness settings, CLAUDE.md, agents, quota and ccloop. Companion article: [[compiled-ccenv-mcp-ccmemory-and-ccprospect-notes]].

## Commit attribution is two controls
Source: `commit-attribution-is-two-controls`
- Claude Code appends two things to commit messages: `Co-Authored-By: Claude ... <noreply@anthropic.com>` (governed by `includeCoAuthoredBy`) and `Claude-Session: https://claude.ai/code/session_...` (governed by `attribution.sessionUrl`, default TRUE).
- `includeCoAuthoredBy: false` alone removes only the first. Verified live 2026-09-10: the injected attribution reminder dropped the co-author line but kept `Claude-Session:`; both vanished only after `attribution.sessionUrl: false`. The session URL matters most: it writes a session identifier into git history and pushes it to any remote.
- Key-name trap: `coAuthoredBy` (no `include`), recommended by blogs and a web search summary, is REJECTED by CLI 2.1.267: `Settings validation failed: - : Unrecognized field: coAuthoredBy`. The Edit tool validates settings.json against the CLI schema so it fails loudly. Schema is embedded in the binary at `~/.local/share/claude/versions/<ver>`; read with `grep -ao "Claude-Session.\{0,60\}"`. `includeCoAuthoredBy` is marked deprecated yet is the only working key.
- Both live in `~/.claude/settings.json`. ccenv v0.32.0's `settings` step seeds four keys: `includeCoAuthoredBy: false`, `attribution.commit: ""`, `attribution.pr: ""`, `attribution.sessionUrl: false`; seeded per sub-key so a deliberately set commit trailer survives.
- Search gotcha: do not grep the ~100MB ELF with greedy fixed-width lookbehind like `grep -ao ".\{700\}sessionUrl"`; it backtracks catastrophically. Anchor on the literal and take trailing context.

## Shipped agents embed no rules
Source: `shipped-agents-embed-no-rules`
- ccenv v0.31.0 added a RULE FOUR bullet: subagents inherit CLAUDE.md as of SESSION START (`.claude/agents/*.md` registered then too), so any rule a delegated task could violate must be embedded verbatim in the agent definition.
- None of the four shipped definitions in `/src/ccenv/agents/` does: `grind.md` (Bash/Read/Grep/Glob, model sonnet; its rules are reporting discipline: raw evidence, never silently truncate, batch aggressively; nothing on git ban, `find /` ban, `rm` shape, `python3`; highest risk, a pure shell runner), `scout.md`, `miner.md`, `memory-compactor.md` (same, narrower blast radius).
- Not currently broken in practice (inheritance covers pre-session rules); breaks quietly for a rule authored mid-session.
- Rules a delegated task could violate: git ban (RULE TWO), `find /` ban (box overlay already restates for subagents), permission-prompt/`rm` shape (RULE SEVENTEEN), `python3` (RULE SIXTEEN), scripts-never-in-`/tmp` (RULE FIVE). Precedent phrasing: "This applies to subagents too — do not delegate a `find /` you are not allowed to run yourself."
- Not done: the user's v0.31.0 instruction was to add the two audited items and nothing adjacent; the gap was reported instead.

## Quota: two weekly buckets and context weighting
Source: `quota-two-weekly-buckets-and-context-weighting`
- Measured from `/usage` and the 2.1.239 binary, 2026-08-22: Current session (5-hour) 15%; Current week (all models) 19%; Current week (Fable) 25%.
- CLI tracks six windows (`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage`) via `GET /api/oauth/usage` (`fetchUtilization`).
- A sonnet subagent request cannot debit the Fable bucket (settles the delegation proposal's premise). It is a transfer: a Fable request debits both buckets, sonnet only all-models. Delegation pays until all-models binds; at the measured mix Fable binds (25% vs 19%).
- `get_context_usage` / statusline expose only `five_hour` and `seven_day`; per-model windows never reach them, so the loop's meter cannot see the bucket that exhausts. Use `/usage`.
- A +50% weekly-limits promo ran through 2026-08-31; headroom measured then is inflated; do not calibrate thresholds on it.
- Cost is context-weighted: `/usage` says "89% of your usage was at >150k context. Longer sessions are more expensive even when cached." The mxfs cost-audit's "~4,430 Fable requests/week" and request-count shares are proxies from one stable context profile; reject "every request costs the same regardless of size".
- Delegation's real mechanism: grind output never enters the parent context, lowering context growth per request and extending work inside the unchanged 500k cutoff.
- Full write-up `/src/ccenv/docs/context-economics.md` (also: gate subagent pass-through on `agent_id` not `agent_type`; `PreToolUse` supports non-blocking `additionalContext`; a denied tool call has already cost its request).

## Handoff docs: freshness stamping SUPERSEDED
Source: `handoff-docs-must-be-freshness-stamped`
- Superseded by `never-make-sessions-maintain-handoff-docs`. ccenv v0.21.0 deleted the handoff module, its two `CCLOOP_HANDOFF_*` env vars, the freshness/STALE machinery and the prompt instruction to maintain a handoff doc under `<project>/.ccloop/`. The filename is deliberately not written anywhere in the tree (a session reading it goes looking for the file).
- The 0.20.0 reasoning asked how to make a model-maintained handoff trustworthy; the answer was not to have one. ccloop names the transcript in the prompt (`runner.prior_session_block`). User: "cost me millions in tokens."
- Durable findings: a doc the outgoing model must remember to update stops being updated (mxfs `state.md` sat 7 days stale across 36 ccloop runs while `state.sh` beside it ran fresh; hence `state.py` is a computed hook). A stale hand-written doc is byte-identical to a fresh one; only mtime differs. Render a problem INTO the block, never swallow it (`state.py` discipline). Audit every scraper for a cap: `files_edited` was the only unbounded one, capped at 60; `last_text` caps at 4,000 chars and hitting it every session proves it was never a summary. `Last 20 bash commands` (465-803 tok, a third of the resume) cut in 0.20.0 and stays cut. `summarize()` runs in the runner AFTER a session exits; the relay is event-driven on the wall event; no "cutoff hook" generates the resume (token cutoff is only an early-relay knob).
- Dead: tiering rules, mtime-vs-session-start check, STALE marker, and the "1,284 tok (no handoff) -> 396 (fresh handoff)" result, which priced only the prompt and never the output tokens spent rewriting the file.

## Repo CLAUDE.md is the managed-block source
Source: `repo-claude-md-is-managed-block-source`
- `assemble_ccenv_base_claude_md()` in install.sh (runs FIRST) rebuilds the `# [CCENV MANAGED]` region of `~/.claude/CLAUDE.md` each install: marker header, verbatim `cat "$SCRIPT_DIR/CLAUDE.md"`, overlay blocks, closing marker. Content outside (e.g. `[AWARENESS PROTOCOL]`) preserved.
- An edit inside the managed region survives only to the next install; policy changes must ALSO land in `/src/ccenv/CLAUDE.md` (also the repo's own project instructions). Keep byte-identical: install.sh `cmp`s and no-ops when equal. Verify: extract region between markers, drop the 2 header-comment lines, diff against repo file.
- Precedent v0.13.3: temp-file rule edited in place in `~/.claude/CLAUDE.md`, later back-ported to repo CLAUDE.md, VERSION 0.13.2 -> 0.13.3, CHANGELOG + docs/install.md entries.
- Related: `install-claude-md-component-owned`.

## ccloop CLI flags thread as params
Source: `ccloop-cli-flags-thread-as-params`
- Established with `--model` (ccloop 0.9.0 / bundle 0.5.0). Runtime config is env-driven (`_config()` reads `CCLOOP_*`) but flags do NOT mutate `os.environ`; they thread as keyword params `cli.main -> runner.cmd_run / cmd_resume -> runner.loop(..., model=None)`, and `loop()` does `cfg = _config(); if model: cfg["model"] = model` (flag beats `CCLOOP_MODEL`).
- Recipe for the next value flag (e.g. `--effort`): (1) `cli.py` `_extract_value_flag(argv, flag, what)` generic popper (`--flag=V` and `--flag V`, last wins, missing-value error); `_extract_cutoff`/`_extract_model` are wrappers; validate in the wrapper (`--model` rejects empty/whitespace so `--model=` doesn't no-op via the falsy `if cfg[...]` in `_build_command`). (2) Thread through BOTH `cmd_run` and `--resume-run`'s `cmd_resume`. (3) Update USAGE (Options + the env var line "(--x flag wins over this)"). (4) Per-invocation like the env var: a resume does not remember the model (unlike `--cutoff`, persisted in `<run-dir>/cutoff` because guard/keepgoing HOOKS in other processes read it).
- Testing: `tests/fake_claude.py` honors `FAKE_ARGS_FILE`, appending each invocation's argv as a JSON line; assert flag-beats-env end to end (`CCLOOP_MODEL=sonnet`, pass `model="opus"`, assert `argv[argv.index("--model")+1] == "opus"`). cli-level parse tests stub `runner.cmd_run`/`cmd_resume` (`_stub_run`/`_stub_resume` in test_cli.py); keep signatures in sync. Never write a seam test whose fake reimplements the override logic.
