# ccprospect — architecture & history

## Origin

Designed 2026-07-12 out of an aitrader investigation into why autonomous
trading agents never anticipate (full thread archived in
`/src/ccenv/prospect.md`; condensed note in
`/src/ccenv/prospective-memory-ledger-design.md`). The empirical core: LLM
agents under procedural prompts generate only *action-terminal* vocabulary —
content some loop slot consumes — and nothing that terminates in a diagnosis.
The fix is to give forward-looking thought an action-terminal encoding: a
contract with a trigger, an expiry, and an intention some future wake
consumes. A GPT consultation (gpt-5.6-sol) contributed the decisive
structural critiques; ccprospect is the domain-neutral generalization (Part
IV of the archive), sibling to ccmemory in the ccenv bundle.

The four-store timescale map: broker/env = NOW · journal = PAST · ccmemory =
TIMELESS · **ccprospect = FUTURE**.

## Design invariants (do not weaken these)

1. **Contracts are immutable; outcomes are inescapable.** Revision =
   `prospect_amend` → successor contract; the original continues
   counterfactual resolution to its own terms until its own expiry.
   `cancel_attention` requires a reason and does not stop resolution.
   Enforced mechanically: O_EXCL contract writes, append-only events.jsonl,
   PreToolUse guard denying hand edits, and a fold that has no mutable
   status field to rewrite.
2. **Two state dimensions, never one status column.** `attention`
   (open/fired/acked/deferred/closed) is what the inbox does with an item;
   `resolution` (pending/done/hit/miss/unresolvable/expired) is what
   happened to the proposition. Conflating them made the original strawman
   gameable (GPT critique §1.1).
3. **Predicates are typed and tiny.** No natural-language conditions, no
   compound booleans in v1 ("no compound predicates until the simple system
   has produced enough failure data"). `cmd_*` is the universal escape hatch.
4. **No retroactive success.** Creation refuses predicates already true;
   probes run once at creation to stamp baselines (`path_changed`'s hash).
5. **Caps are attention budgets, not quality gates.** NO-file is always
   legal; never require a creation.
6. **Inbox, not inventory.** Wakes see fired/due/expiring items only —
   reviewing 20 unchanged rows every wake breeds ritual KEEP-spam (proven
   failure mode).
7. **The report is factual.** Denominators always; no adjectives, no
   thresholds, no live per-decision scores, never rank live prospects by hit
   rate. Presentation-induced policy is the failure mode.
8. **Namespace = the repo.** `.ccprospect/` travels with the clone; nothing
   is shared across projects or instances (sharing live hypotheses
   contaminates independence).

## Module map

```
ccprospect/
  util.py        UTC ISO timestamps; parse_iso (3.9-safe Z handling)
  paths.py       CWD-anchored store resolution (ccmemory contract); gitignore self-heal
  contracts.py   immutable contract files: parse/write (O_EXCL), id alloc, prefix resolve
  events.py      events.jsonl append (O_APPEND) / read (corruption-tolerant)
  predicates.py  validate / creation_check (already-true refusal, baselines) /
                 evaluate (stateless; probes bounded 10s + output cap)
  store.py       derive_states fold (attention × resolution) + Store facade:
                 create (gate, caps, budget), evaluate (rate limits, expiry
                 final-evaluation, counterfactuals), inbox, ack, amend,
                 list/get, report. probe_state.json = LOCAL watermarks.
  index_gen.py   PROSPECT.md digest (generated; guarded)
  hooks.py       session (evaluate+inject inbox+aging nudge), stop (regen),
                 guard (deny edits to record files); all fail-open
  installer.py   settings.json hook registration (atomic, self-healing,
                 foreign-safe) — ccmemory's machinery with ccprospect names
  mcp_server.py  module-level dispatch (testable w/o ccenvmcp) + FastMCP app
  cli.py         mcp | hook | install | uninstall | status | inbox | report | where
```

## Lifecycle (derived, never stored)

```
created ──(predicate true at a wake)──▶ fired ──ack──▶ done / resolved / cancelled
   │                                      │  keep ▶ acked      defer ▶ deferred
   │ (expiry, final evaluation first) ──▶ expired
   │ cancel/supersede: attention closes, EVALUATION CONTINUES until expiry
   └────────────────────────────────────▶ counterfactual hit / expired (report)
```

Fires latch (one-shot): a contract fires at most once; every v1 predicate is
effectively latching, so "keep watching after fire" means keeping the ITEM
active (`acked`), not re-arming the predicate — re-arming is an amend.

`session_start` fires only in SessionStart-hook evaluations ≥1s after
creation (mid-session `prospect_inbox()` calls must not fire "next time I
wake here" cues; the 1s margin covers second-truncated timestamps).

Expiry semantics: at the expiry boundary the contract gets ONE final
evaluation (ignoring `min_interval`) so slow-moving probes get their last
chance; only then does it expire. With `CCPROSPECT_NO_PROBES=1` a cmd
contract expires with `probe_skipped: true` recorded — honest, not fake.

## Deviations from the design archive (deliberate)

- **No SQLite index in v1.** The archive sketched `.prospect_index.db*`
  mirroring ccmemory — but ccmemory needs SQLite for FTS5 search, and
  ccprospect has no search: retrieval is due-ness, and deriving state is a
  cheap fold at cap≈20 active. The only local state that must persist is
  probe watermarks + nudge counters → `probe_state.json` (gitignored).
  `index.db*` stays in the gitignore for a future cache if scale demands.
- **events.jsonl is also guarded** (archive named only contracts/ +
  PROSPECT.md): the event log IS the outcome record; hand-editing it would
  be the trivial history-rewrite bypass.
- **`ccprospect inbox`/`report` CLI subcommands** (read-only) beyond
  ccmemory's minimal-CLI philosophy: humans and cron need visibility
  without an MCP client.
- **No dedup rules** (GPT §4.1 was trading-specific): the active cap +
  daily budget + gate carry the anti-spam load in dev workflows.

## Install / wiring

Bundle `install.sh` pip-installs the package, registers MCP name
`ccprospect` (`ccprospect mcp`), and runs `ccprospect install` for the
hooks. Deliberately NOT `alwaysLoad`: the wake-time work is done by the
SessionStart hook (independent of MCP); the tools may load lazily via
ToolSearch. Hooks also autoinstall on first MCP boot (ccmemory pattern).
Depends on `ccenvmcp` by install order, not by declared dependency.

## Per-project binding: the prospect-integrate skill

Machine-wide install makes the tools/hooks available everywhere, but the
hooks self-gate and injection alone doesn't bind a loop. The
`prospect-integrate` skill (`skills/prospect-integrate/SKILL.md`, installed
to `~/.claude/skills/` by install.sh) lands the binding once per project:

- classify: interactive → CLAUDE.md managed block; ccloop → forced-step
  block in the criteria file (it is re-fed at every stop-hook gate);
  custom-loop → locate the constitution via project docs/ccmemory, CONFIRM
  with the user, and emit a diff against the constitution SOURCE (never
  hot-edit a built artifact; deploys stay owner-run).
- persist: `.ccprospect/integration.json` records shape + binding_file
  (committed with the repo — a project fact, unlike the gitignored
  probe_state.json), so re-runs skip discovery and refresh the
  marker-fenced block in place.
- never files a demo prospect (filler is what the caps exist to prevent).

## History

- v0.1.0 (2026-07-12) — initial implementation per the design archive;
  72 tests; wired into bundle install.sh (bundle v0.8.0). Includes the
  prospect-integrate skill for per-project binding.
