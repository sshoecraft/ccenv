# Prospective-memory ledger — the "order above ccmemory" for anticipation (designed 2026-07-12, NOT built)

> Moved 2026-07-12 out of `/src/aitrader/.ccmemory/` (was the ccmemory note
> `prospective-memory-ledger-design`) to live beside its full companion doc
> **`/src/ccenv/prospect.md`** — which holds the complete thread: the originating
> aitrader investigation, the exact GPT prompt, the raw gpt-5.6-sol response
> verbatim, the synthesis, and the ccprospect module proposal. `[[wiki-links]]`
> below refer to memories that remain in `/src/aitrader/.ccmemory/`.

Follow-on to [[journal-vocab-action-terminal-fingerprint]]. Owner asked: what memory
architecture lets ANY model (incl. weak local) do anticipation? Consulted GPT
(gpt-5.6-sol) with the full packaged evidence; this note = the synthesized design.
(First ask_gpt attempt burned all 6000 max_tokens on internal reasoning, empty reply —
reasoning models need ~30k max_tokens to leave room for output.)

## The four-store timescale map
broker = NOW · journal = PAST · ccmemory = TIMELESS lessons · **MISSING: FUTURE** —
open expectations with triggers/invalidations/expiries surviving wakes and relays.
ccmemory is the WRONG substrate for this (prose+FTS, no lifecycle/resolution machinery);
the future store is a new first-class infra sibling of the journal, NOT a ccmemory extension.

## Core design (GPT's material upgrades to the strawman hypothesis-ledger)
1. **Decompose the overloaded row**: forecast CONTRACT (immutable) ≠ watcher EVENTS
   (append-only) ≠ ATTENTION state (mutable) ≠ agent DISPOSITIONS ≠ ACTION links
   (broker order ids only — prose never counts as "acted") ≠ computed OUTCOMES.
   Strawman's single status column conflated resolution/execution/attention → gameable.
2. **The load-bearing invariant**: *the agent may stop paying attention to a forecast,
   but may never rewrite or escape its outcome.* Revision = new contract superseding old;
   the OLD one still resolves counterfactually to its original terms. Kills every gaming
   vector (moved triggers, widened invalidations, abandoning likely losers) mechanically.
3. **Typed predicate language, tiny v1**: BAR_HIGH_GTE / BAR_LOW_LTE / BAR_CLOSE_GTE /
   BAR_CLOSE_LTE only — no NL conditions, no indicators, no compound booleans. Creation
   validation: predicate not already true, tick-aligned, expiry from fixed buckets
   (EOD/next-close/3d/5d/10d), valid_after > evidence_asof (kills retroactive success),
   evidence provenance = bars-query id/hash.
4. **Inbox, not inventory**: each wake reviews only CHANGED items (crossings, due
   reviews, near-expiry) — full inventory review periodic. Prevents ritual KEEP-spam
   (the proven templating failure). Watcher = watermark-based, idempotent, emits
   AMBIGUOUS_SAME_BAR rather than inventing order.
5. **Caps = attention budgets, not quality gates** (legal): ~12 active/instance,
   2/symbol, 3 creations/wake, 8/day. Slot cost gives vague forecasts opportunity cost.
   NO_CONTRACT is always legal — never require a creation per wake (breeds garbage).
6. **Probability buckets 20/40/60/80 (no 50)** upgrade hit-rate → calibration (Brier,
   stratified by trigger-distance/horizon/direction; counterfactual stats on cancelled).
7. **Calibration feedback = legal** (same class as showing fills/P&L) but
   presentation-ruled: periodic factual report, denominators shown, no adjectives, no
   thresholds, no per-decision live scores, no ranking hypotheses by hit rate. Labels
   stay agent-authored; infra never reclassifies from thesis text.
8. **Never share live hypotheses across instances** (anchoring/contamination kills A/B
   independence). Share substrate only: bars cache, resolver code, schema. Namespace by
   instance+account.
9. **Relay-proofing**: handoff summary is NOT authoritative — fresh session rehydrates
   via bootstrap tool (pending events, due reviews, active inventory, watermark), same
   pattern as broker reconcile.
10. **Boundary sharpening (GPT)**: "fact vs opinion is not enough — selection and
    presentation are cognitive interventions even when inputs are factual." Watcher is
    safe because its universe = agent-registered predicates ONLY; it never proposes
    levels. Compression CSV column legal iff universe-wide, fixed disclosed formula,
    neutral name (range_5d_to_20d, NOT compression_percentile), no boolean/label/
    candidate-list. is_coiled=true or setup_quality=87 = over the line.

## Why this fits the vocabulary finding (the theory)
A forecast contract IS an action-terminal object (trigger level, invalidation level,
expiry). The models' generation collapses onto slot-consumable content — so give the
forward diagnosis an action-terminal encoding and it WILL be generated: the wedge gets
expressed as its resolution levels, taxonomy never needed in any prompt. Exploits the
collapse instead of fighting it.

## Loop integration (per house laws: own NEW steps, number cells, NOT-DONE grammar)
- New step after RECONCILE: **RESOLVE THE PROSPECTIVE INBOX** — one disposition row per
  inbox item (KEEP_WATCHING/REPLACE/ACT/PASS/DEFER/CANCEL_ATTENTION); ACT requires a
  broker order id; incomplete until pending=0. Enforce mechanically: creation tool
  REFUSES new contracts while dispositions pending (tool-level refusal = existing
  enforcement pattern, e.g. paper-only adapter).
- New step after SURVEY: **FORM OR DECLINE FORWARD EXPECTATIONS** — inspect
  multi-session bars for up to 3 names; row = evidence_asof · direction · trigger ·
  invalidation · expiry · bucket · CREATE/REPLACE/NO_CONTRACT. (Subsumes the SHAPE-line
  proposal's forced look.)

## ccenv generalization (Part IV of /src/ccenv/prospect.md)
Proposed NEW module **ccprospect** (not a ccmemory extension — opposite retrieval
models: pull-by-relevance vs push-by-dueness; editable prose vs immutable contracts).
Same idioms as ccmemory (file-backed .ccprospect/ travels with repo, SQLite derived
index, MCP server, autoinstalled hooks). Key generalization: **evaluate-on-wake, no
daemon** (SessionStart hook + prospect_inbox tool; ccloop projects call it per cycle).
Domain-neutral predicates v1: at / session_start / path_exists / path_changed /
cmd_ok / cmd_fail / cmd_match (rate-limited probes; everything else is a cmd).
Tools: prospect_file/inbox/ack/amend/list/get/report. Contract = intention (+optional
expect/bucket → forecast). Creation refused while fired items unacknowledged.
aitrader = pilot consumer for non-price intentions; price watcher stays in aitrader
(schema-sharing sibling) unless probes prove sufficient.

## Build order (v1, aitrader flavor)
1 immutable contracts + 4 predicates → 2 idempotent watcher → 3 inbox + tool-refusal
gating → 4 action links → 5 caps/budgets → 6 counterfactual resolution → 7 relay
bootstrap → 8 weekly factual calibration view → 9 only then shape-fact CSV columns →
10 no compound predicates until failure data exists. Substrate prereq: daily+intraday
bars cache (also unlocks the later compression column). Storage: SQLite sibling of
journal.db; likely served by broker MCP (owns the data connection; precedent:
maybe_backfill_equity writes journal). Success metric: hesitation-tax trend.

## Status
Proposed to owner 2026-07-12 — no code, no constitution edit. Deploys owner-run.
Open questions (owner): module name; .ccprospect committed?; cmd probes at
SessionStart default; buckets in v1?; aitrader consume-vs-sibling.
