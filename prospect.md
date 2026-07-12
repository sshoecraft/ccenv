# prospect.md — Prospective Memory for LLM Agents (design archive + ccenv module proposal)

**Written 2026-07-12** as a crash-recovery archive of a design thread that ran in
`/src/aitrader` (host crashed mid-thread due to unrelated kernel work). This file
preserves EVERYTHING: the originating investigation, the exact GPT consultation
(prompt + raw response verbatim), the synthesis, and the generalization design for
ccenv that was asked for immediately before the crash. Nothing in this document is
built yet — it is a proposal awaiting owner decisions (see §6 Open Questions).

Companion files:
- `/src/ccenv/prospective-memory-ledger-design.md` — the condensed design note
  (moved out of aitrader's memory store 2026-07-12)
- `journal-vocab-action-terminal-fingerprint` (memory, `/src/aitrader/.ccmemory/`)
  — the verified empirical finding

---

## Part I — The originating investigation (aitrader, 2026-07-12)

### The question
Owner asked why the autonomous trading agent — with full trading knowledge (it
defines a falling wedge correctly cold) — has NEVER, in any journal entry, named a
chart pattern or employed pattern-recognition-style anticipation.

### Corroborated finding: the vocabulary fingerprint
Scanned both live journal DBs read-only (`/home/{itrader,atrader}/.local/state/aitrader/journal.db`)
with word-boundary regexes. itrader = Claude Opus instance, 347 entries, 2026-06-15→07-12.
atrader = local gemma-4-31B instance, 273 entries, 06-23→07-12. 620 entries total.

| category | itrader (entries) | atrader (entries) |
|---|---|---|
| wedge, triangle, pennant, H&S, double top/bottom, cup&handle | **0, all six** | **0, all six** |
| RSI, MACD, Bollinger | 0 | 0 |
| VWAP · ATR · SMA/EMA | 5 · 1 · 1 (leakage) | 0 |
| breakout / breakdown | 117 / 34 | 23 / 5 |
| support / resistance | 24 / 12 | 13 / 2 |
| consolidation / coiling | 43 / 23 | 18 / 0 |
| higher-low / lower-low | 115 / 16 | 20 / 3 |

All 7 bare "flag" hits were "CONCENTRATION FLAG"/verb usage — zero pattern usage.
Named-pattern count is a **perfect zero across 620 entries on two different models**.

### Attribution (the decisive layer)
Grepped every prompt channel that reaches the live agents (constitution versions,
seed cards, run-dir memory):
- "breakout" appears in NO constitution version — only 3 CAUTIONARY card mentions
  (forex: breakout-chasers lose; crypto: loud breakouts are fakeouts; futures:
  chasing disappoints). Yet 117 entries / 297 occurrences on opus → largely volunteered.
- "coiling" appears in NO prompt file at all → fully volunteered (23 entries).
- higher-low / lower-low / swing / structure = constitution-seeded. "consolidation"
  seeded only in the 41K aggressive constitution build. "squeeze" = card-crypto only.
- The "RSI" grep hit in the live constitution is a substring false positive
  ("reveRSIng"). The only indicator reference in any constitution is one "a fast MA"
  in stop mechanics.

**The empirical law this yields:** the models volunteer un-seeded vocabulary — but
ONLY *action-terminal* words: names for a level or event that some loop slot
consumes (entry trigger cell, stop placement cell, wake-leash imminence).
Classification nouns (wedge) and indicator frameworks (RSI) terminate in a
DIAGNOSIS, and no loop slot consumes a diagnosis → never generated once in 27 days,
either model. Generation collapses onto slot-consumable content under a
compliance-graded procedural prompt.

### Why the agent isn't anticipating (mechanism stack)
1. Identity doesn't elicit ("as a professional trader" is already in the mandate;
   the project separately proved persona framing makes behavior WORSE and unguided
   operation goes passive). Capability ≠ propensity under forced-artifact prompting.
2. Shape is unrepresentable in the data path: the universe survey CSV carries only
   single-session scalars (price, prev_close, pct_1d, pct_intraday, gap_pct,
   rel_vol, range_pos, day_volume, day_notional). A wedge is a multi-day object;
   nothing upstream of per-name bars pulls can hold one, and there the ask is a
   single number (the swing/structure price).
3. The perceptual channel is closed: patterns are cheap for humans via vision
   gestalt; from a numeric array they are deliberate multi-step computation with no
   required slot. BRIEF.md §55 sanctions an optional chart renderer ("turn bars
   into an image so the agent can look") — never built; sandbox python has pandas
   but NO matplotlib.
4. Output economics: ~6 mandated artifacts per cycle, 10-word thesis cap,
   self-templating off its own 347 prior entries (proven: a new table column was
   ignored 14 minutes after deploy), assistant training suppresses unsolicited
   elaboration.

### Is pattern recognition beneficial? (analysis summary)
- The named taxonomy standalone: weak/mixed evidence (Lo/Mamaysky/Wang 2000: some
  conditional information, not reliable standalone profit; pattern-encyclopedia
  stats are subjective; published TA edges decay). Its real human value is as a
  discipline scaffold (trigger/invalidation/target) + reflexivity on watched names.
- Institutions exploit price SHAPE via learned features, not names — Jiang/Kelly/Xiu
  (J. Finance 2023): CNNs on price *images* extract robust signal beyond
  momentum/reversal. The information is real; the taxonomy is a lossy index into it.
- Decomposed, a falling wedge = volatility compression + highs falling faster than
  lows (seller deceleration) + volume dry-up + trend location — each a measurable
  fact with better evidence than the gestalt.
- The genuine gap in THIS system is **anticipation**: the survey ranks by realized
  %move → structurally late (measured "hesitation tax"; worst case: watched a name
  top its own survey six cycles, bought the top). A forming-structure lens is the
  one pre-move capability. Second gap: no forced multi-timeframe zoom-out.
- Risks: low-hit-rate noise; pattern names as thought-terminating pseudo-signals;
  weak model fakes gestalt cells (proven); multi-week pattern horizons vs short
  leash cadence.

### The strawman that went to GPT
A "hypothesis ledger": agent-authored rows (symbol, direction, short thesis,
trigger price(s), invalidation price, horizon/expiry, status
open/triggered/invalidated/expired/acted/abandoned, outcome notes) + a mechanical
level-watcher reporting crossings of agent-stated numbers each wake + a forced
review sub-step + an outcome/calibration record + a bars cache substrate.

---

## Part II — The GPT consultation (verbatim)

**Tool:** `mcp__ask_gpt__query` → OpenAI **gpt-5.6-sol**.
**Mechanics lesson:** first call with `max_tokens=6000` returned EMPTY —
`finish_reason=length`, all 6000 tokens consumed as `thoughts_tokens` (internal
reasoning), zero visible output. Retry with `max_tokens=30000` succeeded
(9,580 output tokens, 2,560 thoughts). **Rule: reasoning models need a large
max_tokens headroom or they think themselves broke before speaking.**

### System prompt sent
> You are a senior systems architect with deep experience in LLM agent
> architectures, memory systems for autonomous agents, and quantitative trading
> infrastructure. Be direct, technical, and concrete. Critique freely — do not
> flatter. Propose specific data models and mechanisms, not generalities.

### User prompt sent (verbatim)

I need a design consultation on memory architecture for an autonomous LLM trading agent. Full context below, then specific questions.

#### The system

A persistent LLM agent (one instance runs Claude Opus-class via subscription; a second runs a local gemma-4-31B via vLLM — the design must work for ANY model, weak local models included) is the ENTIRE decision-maker for a paper trading account. Everything else is deliberately dumb infrastructure: broker MCP (orders/positions/fills), market-data tools (bars, snapshots of the whole tradeable universe written to CSV), a scheduler (blocking waits = the agent's sleep), a journal DB (append-only narrative + positions-of-record + transactions ledger), and a bash/python sandbox. Hard architectural boundary, constitutionally enforced: infrastructure may compute and rank FACTS (%move, volume, dollar notional) but must never contain an opinion — no screeners by edge/quality, no scores, no signals, no thresholds, no strategy code. All cognition happens in the model's reasoning, driven by a system prompt ("constitution") structured as a mandatory loop: reconcile with broker → survey the whole tape → decide/act → protect positions with stops → journal → set next wake (5min–2h cadence). The runtime relays to a FRESH session when context fills (summary handoff, not compaction), so within-session context is ephemeral; durable state lives in the journal, the broker, and a memory system.

#### Empirically proven prompting laws (each paid for with a live failure, verified across both models)

1. Models execute numbered steps with forced written artifacts ("a row with no X = step NOT DONE") and ignore prose they fluently agree with.
2. Enforcement must be its OWN new step/sub-step — a new column grafted onto an already-established table gets ignored (the model templates off its own prior journal entries; 347 prior entries out-vote the instruction).
3. Weak models fake gestalt cells: asked for a "structure price," gemma filled cells with the current price when it had no real answer. Artifact cells must be mechanically verifiable numbers, not judgments, or they get gamed.
4. Persona/identity framing does not elicit capability (a "you are a senior trader" persona rewrite made behavior WORSE; reverted same day). Unguided "trust it to volunteer" also failed (passive, traded only training-data tickers).
5. New finding (yesterday, corroborated across 620 journal entries from both instances over 27 days): the models' analytical vocabulary is exclusively ACTION-TERMINAL. Named chart patterns (wedge/triangle/pennant/H&S/double-top/cup): literally ZERO occurrences, both models. Indicator frameworks (RSI/MACD/Bollinger): zero. Yet "breakout" appears in 117 of 347 entries (opus), "support" 24, "coiling" 23, "higher-low" 115 — and attribution analysis shows "breakout" and "coiling" appear in NO prompt file (fully volunteered). The rule: vocabulary that names a level or event some loop-slot consumes (entry trigger cell, stop placement cell) gets generated; vocabulary that terminates in a diagnosis (a pattern name, an indicator state) is never generated because no slot consumes a diagnosis. Generation collapses onto slot-consumable content under a compliance-graded procedural prompt.

#### The measured gap: anticipation

The loop is structurally REACTIVE. The universe survey ranks by realized %move (single-session scalar columns only: pct_1d, pct_intraday, gap_pct, rel_vol, range_pos, day_notional). The agent arrives late by construction — we measure a "hesitation tax" (first-identified price vs actually-paid price) and it's consistently positive; worst case it watched a name top its own survey six consecutive cycles, then bought at the top of the move. A developing structure (a multi-day volatility compression, a coil, a base forming under a level) is unrepresentable in the survey layer, is only visible if the agent happens to pull that name's bars, and — critically — any observation it makes about a FORMING setup dies with the session relay or is buried in append-only narrative journal text it will never re-read. Each wake is near-memoryless about the futures it previously imagined. Nothing in the loop ever asks "what do you expect to happen next, and what would confirm/deny it?" — so expectations are never formed as first-class objects, never tracked, never scored against outcomes.

#### Current memory stores and their timescales

- Broker: ground truth NOW (positions/orders/fills). Reconciled every wake.
- Journal DB: the PAST — append-only narrative entries, positions-of-record (entry intent in the agent's words), transactions ledger. Written constantly, re-read rarely (per-symbol history is force-surfaced before entries).
- ccmemory: durable LESSONS (weeks-to-months half-life). File-per-fact markdown with YAML frontmatter; generated index; SQLite FTS5 search; hooks auto-inject relevant memories when files are read. Works well for what it is: hard-won per-asset-class evidence cards, bug workarounds, behavioral rules.

Missing: a MIDDLE-timescale, FORWARD-looking working memory — open hypotheses about the future with triggers, invalidations, and expiries, surviving wakes and session relays, cheap to review every cycle, usable honestly by a weak model.

#### My strawman (critique this)

A "hypothesis ledger" as new infra, deliberately shaped like our existing positions-of-record (which works):
- Row = one forward-looking hypothesis, authored ONLY by the agent: symbol, direction, thesis-in-agent's-words (short), watch-levels: trigger price(s), invalidation price, horizon/expiry (ISO time), status (open/triggered/invalidated/expired/acted/abandoned), created-at, updated-at, outcome notes.
- A mechanical level-watcher (infra, factual): each wake, for every OPEN hypothesis, report crossings since last check off cached bars: "h3 stated trigger 3.82; high since last check 3.91 → CROSSED at 14:02". No scoring, no recommendation — a fact report about the agent's own stated numbers. Analogy: a price alert the agent set for itself.
- Loop integration per our laws: a new forced sub-step — review the watcher's crossing report + every open hypothesis (confirm/kill/expire), and the survey/entry steps may write new hypotheses. Entries can reference the hypothesis they execute ("acted"). Expiries auto-flip status (fact).
- Outcome layer: when a hypothesis closes (triggered→acted or invalidated or expired), the ledger records what price did over the stated horizon (fact), building a calibration record the agent (or the human) can later read: "your compression-resolution hypotheses hit 8/23."
- Substrate: a daily+intraday bars cache so shape facts (range compression over N days, converging highs/lows slopes, volume trend) are cheap to compute in the sandbox at scan time; possibly factual shape columns added to the universe CSV (legal — same class as rel_vol).

#### Questions

1. Critique the strawman hard. What breaks — especially for the weak local model? What's missing? Where does it degenerate (hypothesis spam, stale zombies, self-templating on its own old hypotheses, gaming the calibration record)?
2. Data model and lifecycle you'd actually use: exact fields, states, dedup rules, eviction/expiry policy, caps. How do we stop unbounded ledger growth while never silently deleting an open expectation?
3. The boundary problem: how to keep this from becoming a signal engine by the back door (the watcher must report facts about agent-stated numbers, never generate candidates itself). Is auto-flagging "hypothesis-shaped" market states (e.g., a compression-percentile CSV column) still on the legal side, in your view, and where exactly is the line?
4. Binding-for-any-model: given our proven laws (forced artifacts, own-sub-step, verifiable-number cells), what is the MINIMAL loop integration that makes a weak model actually form, review, and act on hypotheses honestly rather than ritually?
5. Cross-session and cross-instance: relay-proofing (fresh sessions must resume the ledger seamlessly), and does sharing any of this between two independent instances (opus vs gemma, separate accounts) make sense or just contaminate?
6. Is there a fundamentally better architecture than a ledger? Prior art worth stealing from: agent world-models, forecast/calibration ledgers (superforecasting tooling), blackboard systems, TA scan-alert pipelines, anything from production trading agents. What would YOU build as "the order above episodic memory" for anticipation?
7. The calibration record inevitably becomes a performance signal about the agent's own judgment. Is feeding that back into the agent's context a boundary violation (an opinion), or the single most valuable thing this system could add? How would you present it without it becoming a self-fulfilling threshold?

Answer with specifics — schemas, state machines, exact loop-step wording where relevant. Assume the implementer knows the codebase; do not explain LLM basics back to me.

---

### RAW GPT RESPONSE (gpt-5.6-sol, verbatim, unedited) — BEGIN

## Bottom line

The ledger is directionally correct, but the strawman conflates four different objects:

1. **Forecast:** what the agent expects to happen.
2. **Watch condition:** the market event that should wake its attention.
3. **Decision/intention:** what it plans to consider doing if the event occurs.
4. **Evaluation:** whether the forecast resolved and what happened afterward.

Do not put all four into one mutable row with one `status`. That will become unauditable and trivially gameable.

I would build a **prospective-state blackboard** whose core primitive is an immutable, mechanically resolvable forecast contract. Around it sit an event watcher, a decision ledger, and a bounded attention queue.

---

# 1. Hard critique of the strawman

## 1.1 `status` is overloaded

This list is not a coherent state machine:

> open / triggered / invalidated / expired / acted / abandoned

These states belong to different dimensions:

- `triggered`, `invalidated`, `expired`: forecast resolution
- `acted`: execution linkage
- `abandoned`: attention-management decision
- `open`: lifecycle/attention state

Examples that break the model:

- A hypothesis triggers, the agent passes, and price later invalidates.
- The agent abandons it before expiry, but the original prediction later succeeds.
- The agent acts before the exact trigger because another fact changed.
- Trigger and invalidation both occur in the same cached bar and ordering is unknown.
- A trigger fires, but the agent must continue watching for a pullback entry.
- An order is submitted but not filled. Is that "acted"?

Use orthogonal state dimensions or separate tables.

## 1.2 Mutable hypotheses destroy calibration

If the agent can update trigger, invalidation, expiry, or thesis in place, it will rewrite history, intentionally or not.

Every material revision must create a new version/contract. The old forecast continues to resolution for evaluation purposes, even if it is removed from the active attention set.

Otherwise you get:

- moving triggers closer to price;
- widening invalidations;
- extending expiries;
- abandoning likely losers;
- changing labels after the fact;
- counting revised successes but not superseded failures.

Narrative text may be editable only by appending notes. Resolving fields must be immutable.

## 1.3 "Trigger crossed" is underspecified

A price level alone is not a predicate. You need exact semantics:

- trade touches level;
- bar high reaches level;
- bar closes above level;
- previous close below and current close above;
- remains above for N bars;
- volume condition;
- regular-hours only or extended-hours;
- adjusted or unadjusted prices;
- which bar interval;
- what happens at the expiry boundary.

For a weak model, keep the supported predicate language very small and typed. Do not accept natural-language conditions that infrastructure cannot resolve.

## 1.4 A "hit rate" is not calibration

"Compression-resolution hypotheses hit 8/23" is only a hit rate. Calibration requires a declared probability or confidence bucket.

Even with probabilities, the statistic is confounded by:

- trigger distance;
- horizon;
- volatility regime;
- symbol population;
- direction;
- cancellation behavior;
- repeated correlated forecasts;
- agent choosing only obvious cases;
- setup label drift.

If you do not collect probability, call it a **resolution rate**, not calibration.

## 1.5 Trigger success and trade success are different

A forecast can be correct while the trade loses. A trade can profit even though the original forecast was poorly specified.

Keep separate:

- forecast resolution;
- agent response latency;
- order/fill quality;
- post-trigger market path;
- trade P&L.

Otherwise the agent will learn the wrong lesson from mixed outcomes.

## 1.6 The weak model will game easy contracts

Expect these degeneracies:

- Trigger set one tick away from current price.
- Invalidation set absurdly far away.
- Expiry made very long.
- Same symbol repeatedly forecast with slightly different levels.
- Generic copied thesis text.
- Confidence always `60%`.
- Forecast created after the event has effectively happened.
- Every interesting survey name gets a hypothesis, creating spam.
- Every loser gets "abandoned" shortly before failure.
- Trigger conditions copied from the last successful template.
- A hypothesis marked "acted" for an unfilled order.

Mechanical defenses are required. Prompting is insufficient.

## 1.7 Reviewing every open hypothesis every wake will create ritual

If there are 20 open rows and only one changed, a weak model will produce 19 templated `KEEP` responses and stop cognitively processing them.

Use an **inbox**:

- new watcher events;
- hypotheses whose `next_review_at` is due;
- hypotheses nearing expiry;
- unresolved required decisions;
- compact summary of all other open hypotheses.

Periodically force a full inventory review, but not every five-minute wake.

## 1.8 "Outcome over stated horizon" needs preregistration

Do not let outcome metrics be selected after resolution.

At creation, define:

- forecast expiry;
- whether trigger must precede invalidation;
- post-trigger measurement horizon;
- reference price;
- regular-hours policy;
- standard metrics to compute.

Infrastructure should then calculate all standard outcome fields, not an agent-selected favorable subset.

---

# 2. Data model and lifecycle

## 2.1 Separate the objects

I would use these primary entities:

1. `forecast_contract`
2. `forecast_event`
3. `attention_item`
4. `agent_disposition`
5. `action_link`
6. `forecast_outcome`

The forecast contract is immutable. Attention and decisions are mutable through append-only events.

---

## 2.2 Forecast contract schema

Illustrative schema:

```sql
CREATE TABLE forecast_contract (
    forecast_id           UUID PRIMARY KEY,
    namespace_id          TEXT NOT NULL,       -- instance/account boundary
    agent_id              TEXT NOT NULL,
    model_id              TEXT NOT NULL,
    session_id            TEXT NOT NULL,

    instrument_id         TEXT NOT NULL,       -- canonical, not display symbol
    display_symbol        TEXT NOT NULL,
    venue                  TEXT,
    direction             TEXT NOT NULL CHECK (
                              direction IN ('UP', 'DOWN')
                           ),

    thesis_text           TEXT NOT NULL,       -- max ~240 chars
    setup_label           TEXT,                -- agent-authored; optional
    evidence_artifact_id  TEXT,                -- bars/query snapshot hash
    evidence_asof         TIMESTAMPTZ NOT NULL,

    trigger_predicate     JSONB NOT NULL,
    invalidation_predicate JSONB NOT NULL,

    created_at            TIMESTAMPTZ NOT NULL,
    valid_after           TIMESTAMPTZ NOT NULL,
    expires_at            TIMESTAMPTZ NOT NULL,

    probability_bucket    SMALLINT CHECK (
                              probability_bucket IN (20, 40, 60, 80)
                           ),

    post_trigger_horizon  INTERVAL NOT NULL,
    market_hours_policy   TEXT NOT NULL,
    price_adjustment_policy TEXT NOT NULL,
    data_spec_version     TEXT NOT NULL,

    predecessor_id        UUID REFERENCES forecast_contract(forecast_id),
    replacement_reason    TEXT,

    contract_state        TEXT NOT NULL CHECK (
                              contract_state IN (
                                'ACTIVE',
                                'SUPERSEDED',
                                'CANCELLED',
                                'RESOLVED'
                              )
                           ),

    created_price         NUMERIC NOT NULL,
    created_trigger_distance_pct NUMERIC NOT NULL,
    created_invalidation_distance_pct NUMERIC NOT NULL,

    row_version           INTEGER NOT NULL DEFAULT 1
);
```

### Predicate format

Keep version 1 deliberately narrow:

```json
{
  "type": "BAR_HIGH_GTE",
  "level": 3.82,
  "bar_interval": "1m",
  "session": "REGULAR"
}
```

Supported primitives might be only:

- `BAR_HIGH_GTE`
- `BAR_LOW_LTE`
- `BAR_CLOSE_GTE`
- `BAR_CLOSE_LTE`

Do not initially support arbitrary Boolean expression trees, slopes, RSI, "holds above," or natural-language predicates. Those will either turn infrastructure into strategy code or create resolver ambiguity.

For a basic upside forecast:

- trigger: `BAR_HIGH_GTE 3.82`
- invalidation: `BAR_LOW_LTE 3.51`
- proposition: trigger occurs before invalidation and before expiry

The proposition semantics should be fixed by contract version, not restated by the model.

## 2.3 Creation-time validation

Reject a contract mechanically when:

- symbol/instrument cannot be resolved;
- trigger or invalidation is not aligned to tick size;
- expiry is outside permitted horizon buckets;
- trigger predicate is already true at `evidence_asof`;
- invalidation predicate is already true;
- `valid_after <= evidence_asof`;
- referenced bars are stale;
- thesis exceeds length;
- probability is not in an allowed bucket;
- active-cap or creation-budget is exceeded;
- an equivalent forecast already exists;
- trigger and invalidation are identical;
- required market-data provenance is missing.

Set:

```text
valid_after = first eligible market-data timestamp strictly after creation
```

This prevents retroactive success.

Do **not** reject because a level is "too close," "too far," or "unreasonable." Those are opinions. Record the distances and stratify evaluation by them.

---

## 2.4 Event table

```sql
CREATE TABLE forecast_event (
    event_id              UUID PRIMARY KEY,
    forecast_id           UUID NOT NULL REFERENCES forecast_contract,
    event_type            TEXT NOT NULL CHECK (
                              event_type IN (
                                'TRIGGER_OBSERVED',
                                'INVALIDATION_OBSERVED',
                                'EXPIRED_NO_TRIGGER',
                                'AMBIGUOUS_SAME_BAR',
                                'DATA_GAP',
                                'CORPORATE_ACTION',
                                'SUPERSEDED',
                                'CANCELLED',
                                'ACKNOWLEDGED'
                              )
                           ),
    observed_at           TIMESTAMPTZ NOT NULL,
    source_bar_start      TIMESTAMPTZ,
    source_bar_end        TIMESTAMPTZ,
    source_bar_id          TEXT,
    observed_value        NUMERIC,
    predicate_level       NUMERIC,
    data_version          TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    UNIQUE (forecast_id, event_type, source_bar_id)
);
```

The watcher must be:

- idempotent;
- watermark-based;
- replayable;
- data-versioned;
- explicit about gaps;
- split/corporate-action aware.

If trigger and invalidation both occur inside a bar and sequence cannot be established, emit `AMBIGUOUS_SAME_BAR`. Do not invent ordering.

Use finer bars if available, but preserve ambiguity if it remains.

---

## 2.5 Attention state

Forecast resolution and agent attention should not be the same state.

```sql
CREATE TABLE attention_item (
    forecast_id           UUID PRIMARY KEY REFERENCES forecast_contract,
    attention_state       TEXT NOT NULL CHECK (
                              attention_state IN (
                                'WATCHING',
                                'EVENT_PENDING',
                                'DECISION_DEFERRED',
                                'CLOSED'
                              )
                           ),
    next_review_at        TIMESTAMPTZ NOT NULL,
    last_reviewed_at      TIMESTAMPTZ,
    event_deadline_at     TIMESTAMPTZ,
    close_reason          TEXT,
    row_version           INTEGER NOT NULL
);
```

`CLOSED` means "do not keep putting this in the active inbox." It does not erase or censor forecast resolution.

---

## 2.6 Agent disposition

```sql
CREATE TABLE agent_disposition (
    disposition_id        UUID PRIMARY KEY,
    forecast_id           UUID NOT NULL REFERENCES forecast_contract,
    event_id              UUID REFERENCES forecast_event,
    agent_id              TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,

    disposition           TEXT NOT NULL CHECK (
                              disposition IN (
                                'KEEP_WATCHING',
                                'REPLACE',
                                'CANCEL_ATTENTION',
                                'ACT',
                                'PASS',
                                'DEFER'
                              )
                           ),
    next_review_at        TIMESTAMPTZ,
    reason_text           TEXT NOT NULL,
    replacement_forecast_id UUID
);
```

A replacement creates a new immutable contract. The old contract becomes `SUPERSEDED` operationally but continues counterfactual resolution until its original expiry.

That is critical: replacement cannot erase a bad forecast.

---

## 2.7 Action linkage

```sql
CREATE TABLE action_link (
    action_link_id        UUID PRIMARY KEY,
    forecast_id           UUID NOT NULL REFERENCES forecast_contract,
    broker_order_id       TEXT,
    transaction_id        TEXT,
    action_type           TEXT NOT NULL CHECK (
                              action_type IN (
                                'ORDER_SUBMITTED',
                                'ORDER_FILLED',
                                'ORDER_CANCELLED',
                                'POSITION_INCREASED',
                                'POSITION_REDUCED',
                                'POSITION_CLOSED',
                                'PASS'
                              )
                           ),
    occurred_at           TIMESTAMPTZ NOT NULL,
    price                 NUMERIC,
    quantity              NUMERIC
);
```

Only a broker-confirmed event counts as a fill. "Acted" should never be manually inferred from prose.

---

## 2.8 Outcome schema

```sql
CREATE TABLE forecast_outcome (
    forecast_id           UUID PRIMARY KEY REFERENCES forecast_contract,

    resolution            TEXT NOT NULL CHECK (
                              resolution IN (
                                'HIT',
                                'MISS_INVALIDATED',
                                'MISS_EXPIRED',
                                'AMBIGUOUS',
                                'UNRESOLVABLE'
                              )
                           ),
    resolved_at           TIMESTAMPTZ,
    resolution_event_id   UUID REFERENCES forecast_event,

    trigger_price         NUMERIC,
    trigger_time          TIMESTAMPTZ,
    invalidation_price    NUMERIC,
    invalidation_time     TIMESTAMPTZ,

    signed_return_end     NUMERIC,
    signed_mfe_pct        NUMERIC,
    signed_mae_pct        NUMERIC,
    time_to_trigger_sec   BIGINT,
    time_to_mfe_sec       BIGINT,

    acted                 BOOLEAN NOT NULL,
    first_order_latency_sec BIGINT,
    first_fill_latency_sec  BIGINT,
    first_fill_slippage_pct NUMERIC,

    computed_at           TIMESTAMPTZ NOT NULL,
    outcome_spec_version  TEXT NOT NULL
);
```

All standard outcomes should be computed whether favorable or unfavorable.

---

# 3. States and transitions

## 3.1 Forecast resolution

Conceptually:

```text
ACTIVE
  ├─ trigger first       → RESOLVED / HIT
  ├─ invalidation first  → RESOLVED / MISS_INVALIDATED
  ├─ expiry first        → RESOLVED / MISS_EXPIRED
  ├─ same-bar ambiguity  → RESOLVED / AMBIGUOUS
  └─ irrecoverable data  → RESOLVED / UNRESOLVABLE
```

`SUPERSEDED` and `CANCELLED` are operational annotations, not excuses to stop measuring the original proposition.

## 3.2 Attention lifecycle

```text
WATCHING
  ├─ watcher event       → EVENT_PENDING
  ├─ scheduled review    → remains WATCHING after KEEP
  ├─ replacement         → CLOSED
  ├─ cancel attention    → CLOSED
  └─ forecast resolution → EVENT_PENDING

EVENT_PENDING
  ├─ ACT                 → CLOSED or DECISION_DEFERRED
  ├─ PASS                → CLOSED
  ├─ KEEP_WATCHING       → WATCHING
  └─ DEFER               → DECISION_DEFERRED

DECISION_DEFERRED
  ├─ deadline/new event  → EVENT_PENDING
  └─ explicit decision   → CLOSED/WATCHING
```

Do not make `ACTED` a forecast state.

---

# 4. Deduplication, caps, expiry, and eviction

## 4.1 Dedup rules

Start with deterministic rules, not embeddings.

Reject creation when all are true:

- same `namespace_id`;
- same canonical instrument;
- same direction;
- same predicate types;
- trigger levels within one tick or a fixed small price normalization;
- invalidation levels within one tick;
- expiry windows overlap;
- existing forecast remains active.

Also impose:

- maximum one active forecast per symbol and direction;
- maximum two active forecasts per symbol total;
- replacements must reference the predecessor;
- no new forecast for the same symbol/direction in the same wake unless replacing.

Semantic thesis deduplication is unreliable and easy to evade. Structured fields should carry the identity.

## 4.2 Concrete initial caps

I would start with:

- `12` active forecasts per instance;
- `2` active forecasts per symbol;
- `3` creations per wake;
- `8` creations per trading day;
- `3` replacements per trading day;
- expiry restricted to standard buckets:
  - end of session;
  - next session close;
  - 3 trading days;
  - 5 trading days;
  - 10 trading days;
- no active forecast beyond 10 trading days for this loop.

These are attention-budget controls, not quality thresholds. If full, the agent must close or replace an existing item before adding another.

## 4.3 Expiry

Expiry should be automatic, but resolution must account for data completeness.

At expiry:

1. Wait until all expected bars through the expiry boundary are present.
2. Resolve to hit/miss/ambiguous.
3. If bars are missing, enter a computed `PENDING_DATA` condition rather than pretending it expired cleanly.
4. If permanently unavailable, resolve `UNRESOLVABLE`.

## 4.4 Eviction and retention

Never delete open or unresolved records.

Use storage tiers:

- **Hot:** active attention items and forecasts pending outcome computation.
- **Warm:** resolved forecasts from the last 30–90 days.
- **Cold:** all older contracts, events, decisions, and outcomes in compressed/partitioned tables or Parquet.
- **Prompt-visible:** inbox plus a small active inventory, not the cold ledger.

The DB can grow indefinitely at this scale. The actual unbounded-resource problem is context and attention, not disk.

---

# 5. Preventing spam, zombies, and gaming

## 5.1 Slot cost

An active forecast consumes one of 12 slots until:

- resolved;
- explicitly replaced;
- attention closed;
- expired.

Long, vague forecasts therefore have opportunity cost.

## 5.2 No silent abandonment

`CANCEL_ATTENTION` is allowed, but:

- requires a reason;
- does not stop forecast evaluation;
- appears in calibration statistics;
- is reported separately as "cancelled before resolution."

Track resolution rates for:

- all forecasts;
- non-cancelled forecasts;
- cancelled forecasts counterfactually.

Selective cancellation will become visible.

## 5.3 Do not inject old thesis prose by default

Self-templating is a serious risk. The active inventory should show mostly structure:

```text
H7 XYZ UP
trigger: high >= 3.82
invalidate: low <= 3.51
expiry: 2026-07-15 20:00Z
p: 60
state: WATCHING
age: 1.4d
last review: 47m
```

Show thesis text only when:

- the item has an event;
- the agent explicitly opens it;
- a full inventory review is due.

Do not show prior resolved hypotheses during ordinary creation. That will induce copying.

## 5.4 Evidence provenance

Require each new forecast to reference a market-data artifact:

- bars query ID;
- snapshot timestamp;
- file hash or cached dataset version.

The infrastructure need not judge the evidence. It only verifies that the bars existed and were current when the forecast was authored.

## 5.5 Confidence gaming

Use fixed probability buckets:

```text
20 / 40 / 60 / 80
```

Avoid `50`, which becomes a default escape hatch, and avoid false precision.

Expect weak models to cluster at `60`. That is still measurable. Report:

- bucket usage;
- hit rate by bucket;
- Brier score;
- sharpness/distribution;
- cancellation rate by bucket.

Do not automatically change behavior based on those statistics.

---

# 6. Boundary: fact computation versus signal engine

Your current boundary is philosophically unstable. Ranking by realized percentage move already allocates attention and therefore embeds a policy. "Fact" versus "opinion" is not enough; **selection and presentation are cognitive interventions even when every input is factual**.

Use an operational boundary instead.

## 6.1 Infrastructure may

- compute deterministic descriptive transforms;
- compute them for the whole eligible universe;
- expose formulas and parameter versions;
- report events against agent-authored predicates;
- sort by a user/agent-selected factual column;
- maintain caches;
- compute outcomes from preregistered contracts;
- report exact aggregate statistics.

## 6.2 Infrastructure may not

- originate symbols to watch based on expected edge;
- label a state as a buy/sell/setup;
- combine features into a quality score;
- optimize lookbacks against forward returns;
- set trigger or invalidation levels;
- choose a direction;
- suppress candidates based on strategy logic;
- recommend action from a crossing;
- rank hypotheses by estimated profitability;
- generate forecasts from market state.

## 6.3 Compression columns

A universe-wide numeric column such as:

```text
range_5d / range_20d
```

or:

```text
percentile of current 5d realized range within trailing 252 sessions
```

is still a descriptive fact. It is on the legal side if:

- calculated for every symbol;
- formula is fixed and disclosed;
- no threshold or Boolean label is emitted;
- no candidate list is generated from it;
- parameters were not optimized for future returns;
- the agent chooses whether and how to consume it.

A column named `compression_percentile` is defensible, though the name embeds interpretation. A more neutral name is:

```text
range_5d_to_20d
range_5d_percentile_252d
```

This crosses the line:

```text
is_coiled = true
setup_quality = 87
compression_breakout_candidate = true
top_prebreakout_names.csv
```

The boolean threshold and candidate selection are strategy logic.

The watcher is safe because its universe is limited to predicates explicitly registered by the agent. It must never propose new predicates or nearby levels.

---

# 7. Minimal loop integration for weak models

Prompt text alone is not enough. The runtime should gate progression based on tool state.

Use two independent steps: one for reviewing existing prospective state and another for forming new forecasts.

## 7.1 Step: prospective inbox review

Place this after broker reconciliation and before new decisions.

Exact wording:

> **STEP 2 — RESOLVE THE PROSPECTIVE INBOX**
>
> Call `hypothesis_inbox()`.
>
> For every returned row, submit exactly one disposition with:
>
> `forecast_id | event_id_or_NONE | observed_value | stated_level | disposition | next_review_at_or_NONE`
>
> Allowed dispositions:
>
> `KEEP_WATCHING | REPLACE | ACT | PASS | DEFER | CANCEL_ATTENTION`
>
> If `REPLACE`, create the replacement contract and include its ID.
>
> If `ACT`, link the resulting broker order ID. An intention without an order ID is not ACT.
>
> **This step is incomplete until `hypothesis_inbox()` returns `pending_count = 0`. Do not proceed to survey or trading while pending_count is nonzero.**

The infrastructure should enforce the final sentence. Do not trust the model.

For event rows, include mechanically populated values so the model cannot fake them:

```text
forecast_id
event_id
event_type
observed_at
observed_value
predicate_type
stated_level
expiry
current_position
existing_open_order
```

## 7.2 Step: prospective formation

This must be its own later step, after the whole-tape survey.

> **STEP 4 — FORM OR DECLINE FORWARD EXPECTATIONS**
>
> Inspect multi-session bars for up to three selected symbols.
>
> Write one row per inspected symbol:
>
> `symbol | evidence_asof | future_direction | trigger_type | trigger_price | invalidation_type | invalidation_price | expiry | probability_bucket | action`
>
> `action` must be one of:
>
> `CREATE | REPLACE | NO_CONTRACT`
>
> `CREATE` and `REPLACE` are valid only after the contract tool returns a forecast ID.
>
> `NO_CONTRACT` is permitted. Do not invent a forecast merely to fill a slot.
>
> A row without `evidence_asof`, numeric trigger, numeric invalidation, and expiry does not count as an inspected forward expectation.

This forces a future-oriented artifact without requiring spam.

A model can still ritualistically choose `NO_CONTRACT`, but that is preferable to fake hypotheses. Measure:

- inspected symbols;
- creation rate;
- subsequent moves among declined symbols;
- repeated use of `NO_CONTRACT`.

Do not require at least one creation per wake. That directly incentivizes garbage.

## 7.3 Full inventory review

Once per session or every two hours:

> Review the full active inventory. For each active forecast, emit only:
>
> `forecast_id | KEEP | REPLACE | CANCEL_ATTENTION`
>
> Any `REPLACE` requires a new contract ID.

This is separate from event processing.

## 7.4 Triggered forecasts need bounded decisions

A trigger should enter the inbox with a deadline, for example next wake or 15 minutes, whichever comes first.

The agent must choose:

- `ACT`
- `PASS`
- `DEFER` with a concrete next review time

Repeated indefinite deferral should be mechanically capped.

---

# 8. Relay-proofing

The handoff summary should not contain the authoritative hypothesis state. The fresh session must rehydrate it from tools.

At session start:

1. Reconcile broker.
2. Call `hypothesis_bootstrap()`.
3. Receive:
   - pending events;
   - due reviews;
   - compact active inventory;
   - unresolved action links;
   - current slot budget;
   - last processed event watermark.
4. Process the prospective inbox before any new survey or order.

Example bootstrap:

```json
{
  "namespace": "gemma-account-1",
  "pending_events": 2,
  "due_reviews": 1,
  "active_count": 7,
  "slot_limit": 12,
  "unresolved_action_links": 0,
  "full_inventory_review_due": true,
  "data_watermark": "2026-07-12T14:35:00Z"
}
```

The summary handoff may say "seven forecasts active," but IDs and states must be fetched from the authoritative store.

Use optimistic concurrency/version numbers so two tool calls or relay races cannot update the same attention item silently.

---

# 9. Cross-instance sharing

Do not share live hypotheses between Opus and Gemma.

That would destroy experimental independence and cause:

- copied attention;
- copied levels;
- correlated trades;
- stronger-model anchoring of the weaker model;
- inability to attribute discovery;
- feedback loops where one model's prose becomes the other's apparent evidence.

Namespace all contracts by:

```text
instance + agent + account
```

Safe things to share:

- market-data cache;
- predicate resolver code;
- outcome calculation code;
- schema;
- corporate-action handling;
- generic operational bug memories.

Potentially share offline, human-reviewed lessons only after aggregation. Do not automatically inject "Opus is watching XYZ at 3.82" into Gemma.

For research, maintain a human dashboard that compares independent forecasts after the fact. Independence is valuable data; do not contaminate it.

---

# 10. Better architecture than a single ledger

A ledger remains the right storage primitive, but the higher-level architecture should be a **prospective blackboard**, not merely another journal table.

## 10.1 Blackboard layers

### Layer A: facts

- broker state;
- market snapshots;
- cached bars;
- corporate actions;
- factual transforms.

### Layer B: forecast contracts

Immutable statements about future observable events.

### Layer C: attention queue

What must be reconsidered now or later.

### Layer D: decision records

What the agent chose when an event occurred.

### Layer E: action/execution links

Orders, fills, cancellations, position changes.

### Layer F: outcome and calibration views

Mechanical resolution and aggregate history.

This is the "order above episodic memory": **prospective memory plus event-driven attention**.

## 10.2 Relevant prior art to steal from

### Superforecasting systems

Steal:

- explicit resolvable questions;
- fixed closing times;
- probability estimates;
- immutable forecast history;
- revisions as separate timestamped forecasts;
- Brier scoring;
- calibration and sharpness views.

Do not steal open-ended geopolitical question formats. Trading predicates should be much more mechanical.

### Blackboard architectures

Steal:

- shared structured workspace;
- event-triggered activation;
- separate fact, hypothesis, and action objects;
- explicit ownership and provenance.

Avoid autonomous infrastructure agents adding "interpretations." Only the LLM authors forecasts.

### Prospective memory / intention systems

Steal the distinction between:

- time-based intentions: review at 14:30;
- event-based intentions: reconsider if high reaches 3.82.

Your scheduler and watcher naturally implement these.

### Event sourcing

Steal:

- immutable events;
- replayable state;
- versioned reducers;
- auditability;
- no destructive updates.

This is essential for calibration integrity.

### Trading alert systems

Steal:

- typed predicates;
- crossing watermarks;
- market-hours semantics;
- deduplication;
- split adjustment;
- exact-once alert delivery.

Do not steal their scanners, ranking logic, or strategy templates.

---

# 11. Calibration feedback and the constitutional boundary

Feeding the agent factual records of its own forecasts is not an opinion injected by infrastructure. It is analogous to showing fills, slippage, or P&L.

It is also one of the most valuable additions you can make. Without feedback, the forecast ledger becomes ceremonial memory rather than learning infrastructure.

The danger is not boundary violation. The danger is **presentation-induced policy**.

## 11.1 Do not inject live "scores" beside each decision

Avoid:

```text
Your breakout accuracy is only 31%; avoid this trade.
```

That is a recommendation.

Also avoid:

- red/green grades;
- "good/bad" labels;
- leaderboards;
- minimum success thresholds;
- automatic suppression of low-performing setup labels;
- ranking current hypotheses by historical hit rate.

## 11.2 Present periodic factual reports

Once weekly or after a minimum batch size, provide a dedicated review artifact:

```text
Forecasts created: 42
Resolved: 37
Pending: 5
Hit: 18
Miss-invalidated: 11
Miss-expired: 6
Ambiguous: 2

Cancelled before resolution: 9
Counterfactual hits among cancelled: 2
Counterfactual misses among cancelled: 7

Probability bucket:
20: 1/7 hit
40: 4/11 hit
60: 9/14 hit
80: 4/5 hit

Median trigger distance at creation:
Hits: 1.8%
Misses: 4.6%

Median time to trigger: 3.2h
Median decision latency after trigger: 11m
Median first-fill latency after trigger: 17m

Triggered forecasts acted on: 8/18
Triggered forecasts passed: 10/18

Post-trigger signed return:
1h median: ...
session-close median: ...
MFE median: ...
MAE median: ...
```

No adjectives and no recommended threshold.

## 11.3 Stratify before drawing conclusions

At minimum stratify by:

- horizon;
- trigger distance;
- direction;
- liquidity bucket;
- probability bucket;
- acted versus passed;
- cancelled versus retained;
- setup label, only when sample size is shown;
- model/instance.

Always show denominator and unresolved count.

## 11.4 Preserve labels as agent-authored data

Infrastructure may aggregate exact outcomes by `setup_label`, but it must not infer or reclassify labels from thesis text.

Otherwise semantic classification becomes an opinion engine by the back door.

---

# 12. What I would build first

In order:

1. **Immutable forecast contracts** with one trigger, one invalidation, one expiry, one probability bucket.
2. **Idempotent watcher** with typed price predicates and ambiguity handling.
3. **Prospective inbox** with hard runtime gating.
4. **Separate action linkage** to broker orders and fills.
5. **Strict active caps and creation budgets.**
6. **Counterfactual resolution of cancelled/superseded forecasts.**
7. **Relay bootstrap API.**
8. **Weekly factual calibration view.**
9. Only then add multi-window shape facts to the universe CSV.
10. Do not add compound predicates or setup classifiers until the simple system has produced enough failure data.

The key design decision is this:

> An agent may stop paying attention to a forecast, but it may never rewrite or escape the outcome of a forecast it already made.

That rule prevents most of the ledger's likely degeneration while preserving the forward-looking working memory you are missing.

### RAW GPT RESPONSE — END

Raw call metadata: `{"model":"gpt-5.6-sol","finish_reason":"stop","continuations":"0","prompt_tokens":"1990","output_tokens":"9580","thoughts_tokens":"2560","total_tokens":"11570"}`. First attempt metadata (failed empty): `{"error":"OpenAI returned empty content (finish_reason=length)","max_tokens":6000,"thoughts_tokens":"6000"}`.

---

## Part III — Synthesis (Claude), and the aitrader-specific adaptation

### The four-store timescale map
Each store owns a tense. **Broker = NOW. Journal = PAST. ccmemory = TIMELESS
(lessons). Missing = FUTURE.** The missing store is *prospective memory* —
intentions and expectations filed against future events/times, plus event-driven
attention. That is "the order above" episodic memory.

### GPT design points adopted wholesale
1. Decompose the overloaded row: immutable CONTRACT ≠ append-only EVENTS ≠ mutable
   ATTENTION ≠ DISPOSITIONS ≠ ACTION LINKS (broker ids only) ≠ computed OUTCOMES.
2. **The load-bearing invariant:** *the agent may stop paying attention to a
   forecast, but may never rewrite or escape its outcome.* Revision = successor
   contract; the original resolves counterfactually. Kills every gaming vector
   mechanically instead of by prompting.
3. Typed predicate language, tiny v1; creation-time validation incl.
   `valid_after > evidence_asof` (no retroactive success) and evidence provenance.
4. Inbox (changed items only), not inventory-review-every-wake — prevents ritual
   KEEP spam (a proven failure mode).
5. Caps as attention budgets, never quality gates. NO_CONTRACT always legal; never
   require a creation per wake.
6. Probability buckets 20/40/60/80 (no 50) upgrade hit-rate → calibration; report
   counterfactuals on cancelled items so selective abandonment is visible.
7. Calibration feedback is legal (same class as showing fills/P&L) but
   presentation-ruled: periodic, factual, denominators, no adjectives, no
   thresholds, no live per-decision scores, never rank live hypotheses by hit rate.
8. Boundary sharpening: "fact vs opinion is not enough — selection and presentation
   are cognitive interventions even when every input is factual." Watcher legal
   because its universe = agent-registered predicates only. Neutral fact columns
   (`range_5d_to_20d`) legal; `is_coiled=true` / `setup_quality=87` illegal.
9. Never share live hypotheses across instances (kills A/B independence); share
   substrate only; namespace per instance+account.
10. Relay-proofing: handoff summaries are not authoritative — bootstrap tool
    rehydrates; watermark-based idempotent watcher.

### aitrader-specific adaptations (Claude)
- Six Postgres tables → three SQLite tables (immutable `forecast_contract`,
  append-only `forecast_event` folding watcher events + dispositions + order
  links, computed `forecast_outcome`); attention state derivable. The INVARIANTS
  matter, not the table count.
- "Runtime gates until inbox empty" → tool-level refusal in this codebase's idiom:
  the creation tool refuses new contracts while dispositions are pending (same
  enforcement pattern as the paper-only broker adapter), plus the constitution's
  proven NOT-DONE grammar on two NEW steps (inbox-resolve after RECONCILE;
  form-or-decline after SURVEY — the latter subsumes the earlier SHAPE-line
  proposal).
- Likely owner: broker MCP (owns the data connection; precedent: it already writes
  equity backfills into journal.db). Substrate prerequisite: daily+intraday bars
  cache. Success metric: the hesitation tax should shrink.

### Why this fits the vocabulary finding (the unifying theory)
A forecast contract IS an action-terminal encoding of a diagnosis: trigger level,
invalidation level, expiry. The corroborated finding is that these models generate
ONLY action-terminal vocabulary — so give the forward diagnosis an action-terminal
encoding and it WILL be generated: the wedge the model never names gets expressed
as "high ≥ 3.82 before low ≤ 3.51, by Thursday." We exploit the generation
collapse instead of fighting it. That is also why it is model-agnostic: it demands
numbers and enums, never gestalt, and the MEMORY does the remembering — the model
just answers its own mail each wake.

---

## Part IV — Generalization: a ccenv module (the pre-crash question, answered)

Owner's question (verbatim intent): *not* adapting to the aitrader codebase —
adapt to ALL codebases: enhance /src/ccenv with a new module (ccprospective?
ccfuture?) or extend ccmemory with prospective ability. How implemented? What kind
of MCP/tools?

### IV.1 Decision: a NEW module, `ccprospect` — not a ccmemory extension

ccmemory and prospective memory answer opposite questions with opposite mechanics:

| | ccmemory | prospective store |
|---|---|---|
| tense | timeless lessons | future expectations |
| record shape | prose markdown, editable | typed contract, IMMUTABLE + event-sourced |
| retrieval model | PULL — relevance search (FTS5, Read-injection) | PUSH — due-ness (inbox: what fired / what's due NOW) |
| lifecycle | none (write, maybe supersede by hand) | state machine: active→fired→acknowledged→resolved(hit/miss/expired/unresolvable), supersede, counterfactual resolution |
| growth control | compaction (compile-memories) | expiry + caps + tiering; resolved items graduate or archive |

Bolting a state machine, predicates, and immutability onto ccmemory's
file-per-fact prose model would compromise both. Instead: **a sibling package
sharing ccmemory's proven idioms** (single Python package in /src/ccenv, two
dependencies, file-backed records that travel with the repo, SQLite derived
index, one MCP server, hooks autoinstalled on first MCP boot, console script).

**Naming:** `ccprospect` recommended — matches the psychology term (prospective
memory), short, and the noun "prospect" is exactly "a looked-for future."
`ccfuture` is vaguer; `ccprospective` is long. (Owner's call.)

**Composition with ccmemory, not absorption:** when a prospect resolves and the
outcome carries a durable lesson, the agent graduates it into ccmemory
(`memory_write`) — the future store feeds the past store. The SessionStart
injections compose (ccmemory injects relevant lessons; ccprospect injects the due
inbox).

### IV.2 The key generalization insight: evaluate-on-wake, no daemon

aitrader has an always-on loop, so its watcher can be hot. General Claude Code
projects are EPISODIC — sessions start and stop. The generalization is that agents
do not need continuous watching; they need **"remember to check when you wake."**
Human prospective memory works the same way — the intention is dormant until a
retrieval cue.

So ccprospect evaluates predicates at WAKE BOUNDARIES:
- **SessionStart hook** — evaluate all open items (time and path predicates always;
  cmd probes subject to per-item min-interval rate limits), then inject the inbox
  summary as additionalContext: `PROSPECT INBOX: 2 fired, 1 due review, 1 expiring
  <ids+titles>`. This is the "remembering to remember" moment.
- **`prospect_inbox()` tool** — the same evaluation on demand; always-on loop
  projects (ccloop/aitrader) call it every cycle as a forced step.
- **Stop hook** — regenerate the index file; nudge if items have aged unacknowledged
  across N sessions (same pattern as the compile-memories nudge).
- Later, optional, NOT v1: a systemd user timer that evaluates in the background
  and raises a PushNotification for true-async firing.

### IV.3 Storage layout (per project, travels with the repo)

```
.ccprospect/
  contracts/p-0007-node23-pin.md   # one file per contract; IMMUTABLE after creation
  events.jsonl                     # append-only: fired/acks/dispositions/resolutions
  PROSPECT.md                      # GENERATED index/inbox digest (like MEMORY.md)
  .prospect_index.db*              # derived SQLite cache, gitignored
```

- Contract file = YAML frontmatter holding ONLY immutables (id, created_at,
  session_id, title, intention, predicate, expiry, expect?, bucket?, evidence
  note, predecessor_id). Current state is NEVER stored in the contract file — it
  is derived from `events.jsonl` (event sourcing in ccmemory's file idiom).
- A PreToolUse hook BLOCKS direct edits to `contracts/` and `PROSPECT.md` (the
  exact mechanism that protects MEMORY.md today) — immutability enforced by the
  harness, not by convention.
- Human-readable, diffable, clone-portable — same rationale as .ccmemory.

### IV.4 Contract fields (domain-neutral)

```yaml
id: p-0007                     # short deterministic slug (weak models mangle UUIDs
title: revisit Node pin        #   — proven in aitrader; prefix-resolvable ids)
intention: >                   # what to DO when it fires — the prospective payload
  Re-test ensure_node() against Node 23; drop the 22.21.1 pin if CI passes.
predicate:                     # typed, ONE predicate, no booleans in v1
  type: cmd_ok                 # see IV.5
  run: "curl -sf https://nodejs.org/dist/index.json | jq -e '.[0].version|startswith(\"v23\")'"
  min_interval: 86400          # probe at most daily
expires: 2026-10-01T00:00:00Z  # REQUIRED — nothing is open-ended
expect: "our vendored patch will have merged upstream by then"   # OPTIONAL falsifiable claim
bucket: 60                     # OPTIONAL 20/40/60/80 — present only with expect
evidence: "pinned in install.sh@1.7.4 because of npm ENOTEMPTY"  # why this exists
predecessor: null              # set on supersede
created_at: 2026-07-12T21:40:00Z
session: f8f0475d
```

Without `expect`/`bucket` it's a prospective INTENTION (a TODO with a mechanical
retrieval cue and teeth). With them it's a FORECAST that resolves hit/miss and
feeds the calibration report. Both shapes share one lifecycle.

### IV.5 Predicate set v1 (typed, tiny — the trading lesson transplanted)

| type | fields | fires when | evaluated |
|---|---|---|---|
| `at` | `time` | now ≥ time | every wake, free |
| `session_start` | — | next wake in this repo | trivially |
| `path_exists` | `path` | path appears (or `negate`: disappears) | every wake, free |
| `path_changed` | `path`, `baseline_hash` (stamped at creation) | content hash differs | every wake, cheap |
| `cmd_ok` / `cmd_fail` | `run`, `timeout` (≤10s), `min_interval` | probe exit 0 / nonzero | rate-limited |
| `cmd_match` | `run`, `regex`, `timeout`, `min_interval` | output matches | rate-limited |

Everything else (git state, URL state, CI status, a bars level via an HTTP API) is
expressible as a `cmd_*` probe — the universal event source for dev environments.
NO compound predicates in v1 (GPT: "do not add compound predicates until the
simple system has produced enough failure data"). Creation-time validation:
predicate typed and well-formed, expiry required, probe run ONCE at creation to
stamp a baseline and REFUSE a predicate that is already true (no retroactive
success), caps enforced (~20 active per project, soft daily creation budget —
attention budgets, not quality gates).

Probes are the one security/latency surface: bounded timeout, output-size cap,
never run in parallel storms (serial, rate-limited), and a config switch to
disable cmd probes at SessionStart (evaluate only via explicit `prospect_inbox()`)
for paranoid repos.

### IV.6 MCP tool surface (mirrors ccmemory ergonomics)

- `prospect_file(title, intention, predicate, expires, expect?, bucket?, evidence?)`
  → id. Validates per IV.5. REFUSES creation while fired items sit unacknowledged
  (the mechanical gate — same enforcement idiom as aitrader's paper-only adapter).
- `prospect_inbox()` → fired items + due reviews + expiring-soon + counts; each
  fired row carries mechanically populated observed values (the model cannot fake
  them). Empty inbox = cheap no-op.
- `prospect_ack(id, disposition, note?, evidence?)` — dispositions: `done` |
  `keep` | `defer(next_review)` | `cancel_attention` | `resolve(hit|miss|unresolvable)`.
  `evidence` = commit hash / PR URL / path (generalizes GPT's broker-order links).
- `prospect_amend(id, ...)` → NEW successor id; original marked superseded and
  still resolves counterfactually at its original expiry (final probe at expiry).
- `prospect_list(status?)` / `prospect_get(id)` — inventory and detail.
- `prospect_report()` — the factual aging/calibration report: counts by state,
  ages, fired→ack latency, hit/miss by bucket (when buckets used), cancelled
  counterfactuals. No adjectives, no thresholds, denominators always shown.

Hooks (autoinstalled on first MCP boot, ccmemory-style): SessionStart (evaluate +
inject inbox), Stop (regen PROSPECT.md + aging nudge), PreToolUse (block direct
writes to contracts/ + PROSPECT.md).

### IV.7 What carries over from the GPT design vs relaxed

KEPT: immutability + supersede-with-counterfactual-resolution (THE invariant);
typed tiny predicates; inbox-not-inventory; caps as attention budgets; optional
probability buckets + factual periodic report; evidence provenance; creation
refused while acks pending; namespace = the repo (no cross-repo sharing; a clone
carries its own open intentions).

RELAXED/DROPPED (trading-specific): bars cache, tick alignment, market-hours
policy, same-bar ambiguity (generic `unresolvable` covers irresolvable probes),
direction/instrument fields (subsumed by title/intention), mandatory buckets
(optional — most dev prospects are intentions, not forecasts).

### IV.8 Binding honesty (the aitrader lesson, documented per-project)

Hook injection ≠ obedience. ccprospect ships the MECHANISM (inbox, refusal gate,
injection); how strongly it binds is a per-project prompt decision:
- Interactive projects: SessionStart injection + the creation-refusal gate is
  usually enough for a strong model.
- Autonomous loop projects (ccloop): add a forced step to the loop criteria/
  constitution — "call `prospect_inbox()`; one disposition row per item; the step
  is not done until the inbox is empty" — the proven forced-artifact grammar.
ccprospect's README must state this plainly so nobody assumes the tool alone
changes behavior.

### IV.9 Relationship to existing facilities

- **TaskList/TaskCreate** — session-scoped scratch; no persistence, no predicates.
- **CronCreate / scheduled cloud agents** — fire whole agent RUNS on wall-clock
  schedules, cloud-side, not repo-resident, no event predicates, no calibration.
- **ccmemory** — timeless lessons, pull-retrieval.
- **hooks alone** — stateless.
ccprospect is the only repo-resident, file-backed, event-predicated,
outcome-scored FUTURE store. Distinct niche, no overlap to reconcile.

### IV.10 aitrader's relationship to ccprospect (open design decision)

Two options:
- **(a) aitrader consumes ccprospect directly** — bar-level predicates expressed as
  `cmd_match` probes against the local aitrader API. Pro: one implementation.
  Con: tight-cadence watching through subprocess probes is clumsy; the trading
  watcher wants in-process bar access, same-bar ambiguity semantics, broker order
  links.
- **(b) aitrader implements its own specialized prospective store sharing the
  SCHEMA/INVARIANTS (contract/event/outcome, immutability, counterfactual
  resolution) but not the package** — ccprospect stays lean and generic.

Recommendation: build ccprospect generic (v1 predicates only); pilot it in the
aitrader RUN DIR for the agent's non-price intentions ("re-verify the futures-stop
hypothesis Monday", "re-read card-crypto when BTC session flips"); implement the
price-predicate watcher inside aitrader per Part III. If the pilot shows the
generic probe path is sufficient for price levels too, promote a domain
predicate-plugin interface in ccprospect v2 and fold aitrader in.

### IV.11 Open questions for the owner

1. Name: `ccprospect`? (recommended) — or ccfuture / ccprospective.
2. `.ccprospect/` committed to the repo like `.ccmemory/`? (recommended: yes, same
   rationale — clone carries open intentions; index db gitignored.)
3. cmd probes at SessionStart: allowed by default, or opt-in per project
   (`CCPROSPECT_NO_PROBES=1` escape hatch regardless)?
4. Buckets/calibration in v1, or v1.1 after the intention-only core proves out?
5. aitrader: option (a) consume vs (b) schema-sharing sibling (IV.10).

### IV.12 Build order (v1)

1. Package skeleton mirroring ccmemory (pyproject, console script `ccprospect`,
   `ccprospect mcp` entry, hook autoinstall, `.ccprospect/` layout, PROSPECT.md
   generator + write-block hook).
2. Contract files + events.jsonl + derived index; `prospect_file` /
   `prospect_list` / `prospect_get` with creation validation and caps.
3. Predicates: `at`, `session_start`, `path_exists`, `path_changed` (no cmd yet).
4. `prospect_inbox` + `prospect_ack` + SessionStart injection + Stop regen/nudge.
5. `cmd_*` probes with timeouts, output caps, min-interval rate limits, baseline
   stamping, already-true refusal.
6. `prospect_amend` supersede + counterfactual resolution at original expiry.
7. `prospect_report` (factual, stratified, denominators).
8. ccloop integration recipe in docs + the aitrader run-dir pilot.

---

## Status (2026-07-12)

- NOTHING in this document is built. No code written, no constitution edits, no
  deploys (deploys are owner-run by standing rule).
- The aitrader-side proposal (Part III) and the ccenv module proposal (Part IV)
  are independent decisions; Part IV can proceed without Part III and vice versa.
- Authoritative copies: this file (`/src/ccenv/prospect.md`), its condensed design
  note `/src/ccenv/prospective-memory-ledger-design.md` (moved out of aitrader's
  memory store 2026-07-12), and the finding memory
  `journal-vocab-action-terminal-fingerprint` in `/src/aitrader/.ccmemory/`.
