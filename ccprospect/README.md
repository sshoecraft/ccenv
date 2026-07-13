# ccprospect — prospective memory for Claude Code

The FUTURE store. ccmemory remembers what a project *learned* (timeless
lessons, pulled by relevance search); ccprospect remembers what a session
*intended to do when something happens* (future expectations, pushed at you
by due-ness). Sessions are episodic and near-memoryless about the futures
they imagined — an intention like "re-test the Node pin when 23 ships" dies
with the session unless something with teeth carries it forward. ccprospect
is that something.

| | ccmemory | ccprospect |
|---|---|---|
| tense | timeless lessons | future expectations |
| record | prose markdown, editable | typed contract, IMMUTABLE + event-sourced |
| retrieval | PULL — relevance search | PUSH — due-ness (what fired / is due NOW) |
| lifecycle | none | state machine + counterfactual resolution |
| growth control | compaction | expiry + attention caps |

## How it works

A **contract** is an immutable `.md` file in `.ccprospect/contracts/`: a
short title, an **intention** (what the waking session should DO), a typed
**predicate** (the retrieval cue), and a required **expiry**. Optionally an
**expect** (a falsifiable claim) plus a probability **bucket** (20/40/60/80)
— that upgrades the intention to a forecast that resolves hit/miss and feeds
a calibration record.

Predicates v1 (deliberately tiny; everything else is a `cmd_*` probe):

| type | fires when |
|---|---|
| `at` | now ≥ time |
| `session_start` | the next session wakes in this repo |
| `path_exists` | path appears (`negate`: disappears) |
| `path_changed` | content hash differs from the creation-stamped baseline |
| `cmd_ok` / `cmd_fail` | shell probe exits 0 / nonzero |
| `cmd_match` | probe stdout matches a regex |

**Evaluate-on-wake, no daemon.** Predicates are checked at wake boundaries:
the SessionStart hook evaluates everything open and injects a `PROSPECT
INBOX` summary (fired items carry mechanically observed values the model
cannot fake), and the `prospect_inbox()` tool does the same on demand —
always-on loop projects (ccloop) call it every cycle as a forced step.
`cmd_*` probes are hard-capped at 10s, output-capped, strictly serial,
rate-limited per item (`min_interval`, default 1h), and can be disabled at
session start with `CCPROSPECT_NO_PROBES=1`.

**The load-bearing invariant:** a session may stop paying attention to a
prospect, but may never rewrite or escape its outcome. Contracts are
immutable (a PreToolUse hook denies direct edits to `contracts/`,
`events.jsonl`, and `PROSPECT.md`); revision means `prospect_amend`, which
files a successor while the original keeps resolving counterfactually to its
own terms until its own expiry. Cancellation requires a reason and does not
stop resolution either. Every gaming vector — moved triggers, widened
windows, abandoning likely losers — dies mechanically, not by prompting.

**Attention budgets, not quality gates:** at most ~20 active prospects
(`CCPROSPECT_MAX_ACTIVE`) and 8 creations/day (`CCPROSPECT_DAILY_BUDGET`);
creation is refused while fired items sit unacknowledged, and refused when a
predicate is already true at filing (no retroactive success — probes run
once at creation to stamp baselines). Declining to file is always legal.

## Tools (MCP name: `ccprospect`)

- `prospect_file(title, intention, predicate, expires[, expect, bucket, evidence])`
- `prospect_inbox()` — evaluate now; fired + due + expiring + counts
- `prospect_ack(id, disposition[, resolution, note, evidence, next_review])`
  — `done` | `keep` | `defer` | `cancel_attention` | `resolve`
- `prospect_amend(id, ...)` — supersede with a successor
- `prospect_list(status?)` / `prospect_get(id)`
- `prospect_report()` — factual aging/calibration: counts, denominators,
  ack latency, hit/miss by bucket, counterfactuals of cancelled/superseded.
  No adjectives, no thresholds, no advice — by design.

CLI: `ccprospect mcp|hook|install|uninstall|status|inbox|report|where`.
Hooks autoinstall on first MCP boot (or `ccprospect install`).

## Storage — travels with the repo

```
.ccprospect/
  contracts/p-0007-revisit-node-pin.md   immutable contract files
  events.jsonl                           append-only event log (state = fold of this)
  PROSPECT.md                            generated digest — never hand-edit
  probe_state.json                       LOCAL probe watermarks (gitignored)
```

Commit `.ccprospect/` like `.ccmemory/` — a clone carries its open
intentions. The store namespace is the repo; nothing is shared across
projects or instances.

## Binding honesty — read this before assuming behavior changes

ccprospect ships the MECHANISM: the inbox, the mechanically-populated fired
rows, the creation-refusal gate, the SessionStart injection. **Hook
injection is not obedience.** How strongly the inbox binds is a per-project
prompt decision:

- Interactive projects: the SessionStart injection plus the creation gate is
  usually enough for a strong model.
- Autonomous loop projects (ccloop): add a forced step to the loop
  criteria/constitution — "call `prospect_inbox()`; submit one disposition
  per item; the step is not done until `pending_count == 0`" — the proven
  forced-artifact grammar.

Nobody should assume the tool alone changes behavior. It gives intentions a
place to survive and a cue to return; the loop prompt is what makes
answering the mail mandatory.

**The `prospect-integrate` skill** (installed globally by ccenv's
install.sh) lands that binding for you, once per project: it classifies the
project (interactive / ccloop / custom-loop), places a marker-fenced block
in the right surface — project `CLAUDE.md`, the ccloop criteria file, or,
for custom loops with built constitutions, a diff against the constitution
SOURCE that it hands to you (never guessing, never hot-editing a generated
artifact) — and records the decision in `.ccprospect/integration.json` so
re-runs are deterministic. Invoke it inside the target project:
`/prospect-integrate`.

## Composition with ccmemory

When a resolved prospect carries a durable lesson, graduate it:
`memory_write(...)` the lesson into ccmemory. The future store feeds the
past store. The SessionStart injections compose (ccmemory injects relevant
lessons; ccprospect injects the due inbox).
