---
name: ccprospect-module-created
description: ccprospect v0.1.0 (bundle v0.8.0): prospective-memory sibling of ccmemory — immutable contracts + events.jsonl fold, evaluate-on-wake, counterfactual…
metadata:
  type: project
tags: [ccprospect, prospective-memory, architecture]
---

# ccprospect module — built 2026-07-12 per prospect.md Part IV

New core component `/src/ccenv/ccprospect/` (bundle v0.8.0). Full arch doc:
`ccprospect/docs/ccprospect.md`. Design archive: `prospect.md` (do not delete —
it holds the verbatim GPT consultation and the aitrader evidence).

## Invariants that must never be weakened
- Contracts IMMUTABLE (O_EXCL writes, PreToolUse guard); state = fold of
  append-only events.jsonl. TWO state dimensions: attention × resolution.
- Cancel/supersede close ATTENTION, never RESOLUTION — counterfactual
  evaluation continues to original expiry, with one final evaluation at the
  boundary (ignores min_interval).
- Creation refuses already-true predicates (probes baseline once at creation);
  caps are attention budgets (20 active / 8 per day, env-tunable); creation
  gated while fired items unacknowledged (amend is exempt — slot-neutral).
- session_start predicates fire ONLY in SessionStart-hook evaluations ≥1s
  after creation (mid-session inbox calls must not fire them; 1s margin covers
  second-truncated ISO timestamps).
- Fires latch one-shot; "keep" after fire = attention acked, NOT re-arm
  (re-arm = amend).

## Owner decisions made in-session (2026-07-12)
- ccprospect is STANDALONE, consumed by projects — NO hybrid/sibling watcher
  in aitrader (spec IV.10 option (a) direction). Price semantics belong in a
  small factual probe CLI inside aitrader that contracts call via cmd_*.
- Binding delivery = the `prospect-integrate` skill
  (ccprospect/skills/prospect-integrate/SKILL.md, installed by install.sh to
  ~/.claude/skills/): decision tree interactive→CLAUDE.md block /
  ccloop→criteria file (re-fed at every stop-gate) / custom-loop→locate via
  docs+memory, CONFIRM with user, diff against constitution SOURCE (never
  hot-edit built artifacts; deploys owner-run). Decision persisted in
  .ccprospect/integration.json (committed, unlike gitignored probe_state.json).
  Owner will pilot it in /src/aitrader.

## Deliberate deviations from the archive (documented in docs/ccprospect.md)
- NO SQLite index in v1 (no search to serve; state fold is cheap at cap 20).
  probe_state.json = LOCAL gitignored watermarks/nudge counters; index.db*
  pre-gitignored for future use.
- events.jsonl also guarded from hand edits (archive listed only contracts/ +
  PROSPECT.md) — the event log IS the outcome record.
- CLI has read-only inbox/report subcommands beyond ccmemory's minimal-CLI rule.
- NOT alwaysLoad in install.sh — SessionStart HOOK does wake-time work
  independent of MCP; tools load lazily via ToolSearch.

## Gotchas learned building it
- ccenvmcp FastMCP: line-delimited JSON-RPC on stdio; @app.tool(name=,
  description=, schema=) with hand-written schemas (ccmemory pattern).
- mcp_server.dispatch kept MODULE-LEVEL (not a closure like ccmemory's) so the
  tool surface tests run without ccenvmcp installed; validate name against
  SCHEMAS FIRST or unknown tools get masked by the no-store message.
- Python 3.9: datetime.fromisoformat can't parse Z — all parsing through
  util.parse_iso. PEP 604 unions need `from __future__ import annotations`
  everywhere including tests/conftest.
- Store event timestamps: created/fired/expired events carry ts from the
  store's logical clock (now_utc, monkeypatchable) so tests can drive expiry/
  dueness without sleeps; ack events use real time.

72 tests (`python3 -m pytest tests/` from ccprospect/). E2E stdio drive script
pattern: scratch HOME + PYTHONPATH=ccprospect:ccenvmcp, pipe JSON-RPC lines.
