# ccprospect changelog

Per the global rule: patch = fix, minor = feature, major = breaking.

## v0.1.0

Initial implementation, per the design archived in `/src/ccenv/prospect.md`
(Part IV) and `/src/ccenv/prospective-memory-ledger-design.md`:

- Immutable contract files (`.ccprospect/contracts/p-NNNN-<slug>.md`, YAML
  frontmatter, O_EXCL writes) + append-only `events.jsonl`; ALL state
  (attention × resolution, two dimensions) derived by folding the log.
- Typed predicates v1: `at`, `session_start`, `path_exists` (+negate),
  `path_changed` (baseline hash stamped at creation), `cmd_ok`, `cmd_fail`,
  `cmd_match` — probes hard-capped at 10s, output-capped, serial,
  per-item `min_interval` rate limits, `CCPROSPECT_NO_PROBES=1` switch.
- Creation validation: expiry required and future, predicate refused if
  already true (creation probe stamps baselines — no retroactive success),
  bucket (20/40/60/80, no 50) only with an `expect` claim.
- Attention budgets: ~20 active (`CCPROSPECT_MAX_ACTIVE`), 8 creations/day
  (`CCPROSPECT_DAILY_BUDGET`); creation refused while fired items sit
  unacknowledged (the mechanical gate).
- Counterfactual resolution: `cancel_attention` (note required) and
  `prospect_amend` (supersede → successor) never stop evaluation; the
  original resolves to its own terms at its own expiry, with a final
  evaluation at the expiry boundary.
- Evaluate-on-wake: SessionStart hook evaluates + injects the PROSPECT
  INBOX (with an aging nudge for items fired-unacked across sessions), Stop
  hook regenerates PROSPECT.md, PreToolUse guard denies hand edits to
  contracts/, events.jsonl, PROSPECT.md.
- MCP server (ccenvmcp shim): prospect_file / prospect_inbox / prospect_ack
  / prospect_amend / prospect_list / prospect_get / prospect_report.
  Hooks autoinstall on MCP boot.
- Factual `prospect_report`: counts by state, ack latency, hit/miss by
  bucket, cancelled/superseded counterfactuals — denominators always, no
  adjectives, no thresholds.
- 72 tests: predicates, creation rules/caps/gate, full lifecycle incl.
  counterfactuals and expiry-boundary final evaluation, hooks, installer,
  MCP dispatch.
- `prospect-integrate` skill (installed to `~/.claude/skills/` by the bundle
  installer): one-time per-project wiring of the binding surface — project
  CLAUDE.md (interactive), ccloop criteria file (loop variant with the
  forced-step NOT-DONE grammar), or a diff against a custom loop's
  constitution SOURCE (confirm-with-user, never hot-edit a built artifact,
  deploys stay owner-run). Records shape + binding_file in
  `.ccprospect/integration.json` (travels with the repo) so re-runs refresh
  the managed block deterministically.
