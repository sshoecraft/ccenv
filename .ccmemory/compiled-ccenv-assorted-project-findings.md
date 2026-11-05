---
name: compiled-ccenv-assorted-project-findings
description: Assorted ccenv notes (no shared topic): commit attribution keys, agents embed no rules, quota buckets, handoff, list budget, ccprospect, managed bloc…
metadata:
  type: project
tags: [compiled, ccenv, ccmemory, ccloop, ccprospect, mcp, settings]
---

Assorted group: these eleven notes share no topic; they are ccenv/ccmemory/ccloop findings grouped by the clusterer's tail. Sections are independent.

## Commit attribution is two controls

[[commit-attribution-is-two-controls]]
- Claude Code appends `Co-Authored-By: Claude ... <noreply@anthropic.com>` (key `includeCoAuthoredBy`) and `Claude-Session: https://claude.ai/code/session_...` (key `attribution.sessionUrl`, defaults TRUE). `includeCoAuthoredBy: false` alone removes only the first; verified live 2026-09-10; both gone only after `attribution.sessionUrl: false`. The session URL matters most: it writes a session id into git history permanently and pushes it to the remote.
- Key trap: `coAuthoredBy` (blog posts, a web search summary) is rejected by CLI 2.1.267: `Settings validation failed: - : Unrecognized field: coAuthoredBy`. The Edit tool validates settings.json against the CLI schema, so it fails loudly. Schema is in the binary at `~/.local/share/claude/versions/<ver>`; read with `grep -ao "Claude-Session.\{0,60\}"`. `includeCoAuthoredBy` is marked deprecated but is the only working key.
- ccenv v0.32.0 `settings` step seeds four keys per sub-key (a deliberately set trailer survives): `includeCoAuthoredBy: false`, `attribution.commit: ""`, `attribution.pr: ""`, `attribution.sessionUrl: false`, in `~/.claude/settings.json`.
- Search gotcha: never grep the ~100MB ELF with a greedy fixed-width lookbehind like `grep -ao ".\{700\}sessionUrl"` (catastrophic backtracking, never returns); anchor on the literal and take trailing context.

## Shipped agents embed no rules

[[shipped-agents-embed-no-rules]]
- ccenv v0.31.0 added a RULE FOUR bullet: subagents inherit CLAUDE.md as of SESSION START (and `.claude/agents/*.md` registered then), so any rule a delegated task could violate must be embedded verbatim in the agent definition. None of the four in `/src/ccenv/agents/` does: `grind.md` (Bash/Read/Grep/Glob, model sonnet; rules only about reporting discipline: raw evidence, never silently truncate, batch aggressively; nothing on git ban, `find /` ban, `rm` shape, `python3`; highest risk, pure shell runner), `scout.md`, `miner.md`, `memory-compactor.md` (same, narrower).
- Inheritance covers pre-session rules, so not currently broken; breaks quietly for a rule authored mid-session. Rules a delegated task could violate: git ban (RULE TWO), `find /` ban (box overlay already restates for subagents: "This applies to subagents too — do not delegate a `find /` you are not allowed to run yourself."), `rm` shape (SEVENTEEN), `python3` (SIXTEEN), scripts-never-in-`/tmp` (FIVE).
- Not done: user's v0.31.0 instruction was to add the two audited items and nothing adjacent; gap reported instead.

## Quota: two weekly buckets, context-weighted cost

[[quota-two-weekly-buckets-and-context-weighting]] (measured from `/usage` and the 2.1.239 binary, 2026-08-22)
- Used: session (5-hour) 15%, week all models 19%, week (Fable) 25%. CLI tracks six windows (`five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage`) via `GET /api/oauth/usage` (`fetchUtilization`).
- A sonnet subagent request cannot debit the Fable bucket (settles the delegation proposal's premise); a Fable request debits both, sonnet only all-models. Delegation pays until all-models binds; at the measured mix Fable binds (25% vs 19%).
- `get_context_usage`/statusline expose only `five_hour` and `seven_day`; per-model windows never reach them; use `/usage`.
- +50% weekly-limits promo ran through 2026-08-31; headroom measured then is inflated, do not calibrate on it.
- `/usage`: "89% of your usage was at >150k context. Longer sessions are more expensive even when cached." So the mxfs cost-audit's "~4,430 Fable requests/week" and request-count shares are proxies under one context profile; reject "every request costs the same". Delegation's real mechanism: grind output never enters parent context, lowering growth per request and extending work inside the 500k cutoff.
- Write-up `/src/ccenv/docs/context-economics.md` (also: gate subagent pass-through on `agent_id` not `agent_type`; `PreToolUse` supports non-blocking `additionalContext`; a denied tool call has already cost its request).

## Handoff docs: superseded, durable half kept

[[handoff-docs-must-be-freshness-stamped]] (SUPERSEDED by `never-make-sessions-maintain-handoff-docs`)
- ccenv v0.21.0 deleted the handoff module, its two `CCLOOP_HANDOFF_*` env vars, the freshness/STALE machinery and the prompt instruction to maintain a handoff doc under `<project>/.ccloop/`. The filename is deliberately not written anywhere (a session reading it goes looking). The 0.20.0 question "how do we make a model-maintained handoff trustworthy" had the answer "don't have one"; the transcript is complete, free, can't go stale; ccloop names it via `runner.prior_session_block`. User: "cost me millions in tokens."
- Still true: a doc the outgoing model must remember to update stops being updated (mxfs `state.md` 7 days stale across 36 ccloop runs while `state.sh` beside it ran fresh; hence `state.py` is a computed hook). A stale hand-written doc is byte-identical to a fresh one except mtime. Render a problem INTO the block, never swallow it. Audit scrapers for a cap: `files_edited` was the only unbounded one (capped at 60); `last_text` caps at 4,000 chars and hitting it every session proves it was never a summary. `Last 20 bash commands` (465-803 tok, a third of the resume) cut in 0.20.0, stays cut.
- Mechanism: `summarize()` runs in the runner AFTER a session exits; relay is event-driven on the wall event; no "cutoff hook" generates the resume (token cutoff is an early-relay knob only).
- Dead: tiering rules, mtime-vs-session-start check, STALE marker, and the "1,284 tok (no handoff) -> 396 (fresh handoff)" result (priced only the prompt, not the output tokens burned rewriting the file).

## ccmemory list budget must trim every tier (0.19.0)

[[list-budget-must-trim-every-tier]]
- Failure: `memory_list` on mxfs (1,848 memories) shipped ~14.9k tokens vs a 6,000 budget while `memory_stats` said 10,490; zero project notes and zero compiled articles listed; 180 entries all `reference`/`feedback`/`user`; paid before the first message and on every relay.
- Four defects: (1) `Store.list_all` seeded `spent` with the full always-listed set and trimmed only the remainder; the loop broke on the first project note. (2) `reference` in `ALWAYS_LIST_TYPES` (exempt from folding) while `compile._select` ingested only `type='project'`: nothing could ever retire a reference memory (write-only). (3) `compiled-` articles competed on mtime alone: 2 of 132 listed; folding retired 1,494 notes for articles then withheld. (4) `_entry_tokens` modelled `name+description+30` but the server shipped `json.dumps(indent=2)`: 1.42x under-count, inherited by `memory_stats`. Plus `count_backlog` counted every type while only `project` was actionable: backlog floor 144 vs threshold 20, nudge fired every session and could not be silenced.
- Invariants: a budget must trim every tier including the first (else advisory). Keep `ALWAYS_LIST_TYPES` small, now `("user","feedback")`; `reference` is reached by BM25 search. `ALWAYS_LIST_TYPES` and `compile.COMPILABLE_TYPES` stay complementary (`test_compilable_and_always_listed_types_are_complementary`). An unsilenceable alarm is broken; count backlog only over what compile can ingest. Estimator/serializer drift silently: `test_entry_tokens_tracks_real_wire_size`.
- Method: don't trust self-reported cost: `list_tokens_actual` 10,490 vs `len(json.dumps(payload))` 14,921. Probes `tests/measure_list_payload.py`, `tests/simulate_list_policy.py` (opens `index.db` `mode=ro`, simulates policy on real stores; surfaced defect 3).
- Cleared, not a defect: 33 mxfs rows have frontmatter `name:` differing from filename (e.g. `feedback_commits.md` -> `name: Commits only when work is done/working`); index keys on frontmatter name and stores `path`, so `memory_get` resolves; 0 dead paths, 0 unindexed files.

## ccprospect module

[[ccprospect-module-created]] (ccprospect v0.1.0, bundle v0.8.0, built 2026-07-12 per prospect.md Part IV; `/src/ccenv/ccprospect/`, arch doc `ccprospect/docs/ccprospect.md`; `prospect.md` archive holds verbatim GPT consultation and aitrader evidence, do not delete)
- Invariants: contracts IMMUTABLE (O_EXCL writes, PreToolUse guard); state = fold of append-only `events.jsonl`; two dimensions attention x resolution. Cancel/supersede close ATTENTION not RESOLUTION: counterfactual evaluation continues to original expiry with one final evaluation at the boundary (ignores min_interval). Creation refuses already-true predicates (baseline probe); caps are attention budgets (20 active / 8 per day, env-tunable); creation gated while fired items unacknowledged (amend exempt). session_start predicates fire ONLY in SessionStart-hook evaluations >=1s after creation (covers second-truncated ISO timestamps). Fires latch one-shot; "keep" after fire = ack, not re-arm (re-arm = amend).
- Owner decisions 2026-07-12: STANDALONE, no hybrid watcher in aitrader (spec IV.10 option (a)); price semantics in a small factual probe CLI inside aitrader called via cmd_*. Delivery = `prospect-integrate` skill (`ccprospect/skills/prospect-integrate/SKILL.md`, installed to `~/.claude/skills/`): interactive -> CLAUDE.md block; ccloop -> criteria file (re-fed at every stop-gate); custom-loop -> locate via docs+memory, CONFIRM with user, diff against constitution SOURCE (never hot-edit built artifacts; deploys owner-run). Decision in `.ccprospect/integration.json` (committed, unlike gitignored `probe_state.json`). Owner pilots in /src/aitrader.
- Deviations (documented): no SQLite index in v1 (`probe_state.json` local gitignored watermarks/nudge counters; `index.db*` pre-gitignored); `events.jsonl` guarded from hand edits too; CLI has read-only inbox/report beyond ccmemory's minimal-CLI rule; NOT alwaysLoad in install.sh (SessionStart HOOK does wake work; tools lazy via ToolSearch).
- Gotchas: ccenvmcp FastMCP line-delimited JSON-RPC on stdio, `@app.tool(name=, description=, schema=)` hand-written schemas. `mcp_server.dispatch` MODULE-LEVEL so tool tests run without ccenvmcp; validate name against SCHEMAS FIRST or unknown tools get masked by the no-store message. Python 3.9: `datetime.fromisoformat` can't parse Z (use `util.parse_iso`); PEP 604 unions need `from __future__ import annotations` everywhere incl. tests/conftest. Created/fired/expired events use the store's logical clock (`now_utc`, monkeypatchable); ack events real time. 72 tests (`python3 -m pytest tests/` from ccprospect/); E2E: scratch HOME + `PYTHONPATH=ccprospect:ccenvmcp`, pipe JSON-RPC lines.

## Repo CLAUDE.md is the managed-block source

[[repo-claude-md-is-managed-block-source]]
- `assemble_ccenv_base_claude_md()` in install.sh (FIRST, before components) rebuilds the `# [CCENV MANAGED]` region of `~/.claude/CLAUDE.md` every install: marker header, verbatim `cat "$SCRIPT_DIR/CLAUDE.md"`, overlay blocks, closing marker. Outside content (e.g. `[AWARENESS PROTOCOL]`) preserved.
- An edit inside the region survives only until the next install; policy changes must ALSO land in `/src/ccenv/CLAUDE.md` (doubles as the repo's project instructions). Keep byte-identical: install.sh `cmp`s result vs installed file and no-ops if equal. Verify: extract the region, drop the 2 header-comment lines, diff against repo file.
- Precedent v0.13.3: temp-file rule edited in place in `~/.claude/CLAUDE.md`; later back-ported to repo CLAUDE.md, VERSION 0.13.2->0.13.3, CHANGELOG + docs/install.md entries. Related: `install-claude-md-component-owned`.

## MCP alwaysLoad removed (v0.13.2)

[[mcp-alwaysload-blocks-startup]] (install.sh set `alwaysLoad: true` on ccmemory from v0.6.0; `strip_always_load()` now deletes it)
- Correction 1 (2026-07-24, recorded, not acted on), decompiled binary 2.1.219: `o = !su(process.env.MCP_CONNECTION_NONBLOCKING)`; `s = configs.filter(u => u.alwaysLoad === true)` tier "regular-required"; `a = ... !== true` tier "regular"; `Promise.all([ Vyl(false, ...s...), Vyl(o, ...a...) ])`. alwaysLoad tier is always blocking but bounded: `GVu()` = `MCP_CONNECT_TIMEOUT_MS` default 5000, `awaitEachWithDeadline`, then logs `[MCP] <r>: <c>/<n> not ready after <s>ms — proceeding; background connection continues`. Deadline per TIER, shared; session starts anyway; never guaranteed registration.
- Correction 2 (2026-07-25) forced removal: on non-Anthropic models it ERASES the tool surface. atrader@clyde, Claude Code driving `google/gemma-4-26B-A4B-it` via OpenAI-compatible proxy (`chatcmpl-` tool_use_ids), 172 transcripts 2026-06-29..07-25: broker/journal/scheduler/searxng (unset) 353-495 successful calls each; ccusage (unset) 16 sessions 0 rejections; ccmemory (true) 0 successful memory_list ever, 122 transcripts `No such tool available`. ccusage is the control (same bundle, installer, box, proxy, session). Not a deferral (no deferred-tool reminder named them); server healthy (`claude mcp list` Connected, handshake 0.18s, SessionStart hook fired in 5 newest transcripts), the model knew `mcp__ccmemory__memory_list` from injected protocol prose.
- Still true: no hook can gate on MCP status (SessionStart hooks finish BEFORE the init event reporting `mcp_servers`; no post-MCP-connect hook phase, anthropics/claude-code#26112; scheduled/Remote Trigger sessions miss the deferred tool list, #41778). No load order (both tiers one `Promise.all`; `~/.claude.json` key order meaningless; "sentinel server loads last" not implementable). The model can enumerate its MCP surface from its tool list (eager = functions, deferred = named in a system-reminder; split `mcp__<server>__<tool>` on `__`); `claude mcp list` is NOT in-session truth (spawns a second copy of every server). Enumeration cannot detect its own incompleteness; diff against a hardcoded expected set. Setting/removing the field: no `claude mcp add` flag; `get`/`list` don't show it; read `~/.claude.json` directly, rewrite via `claude mcp remove -s user <name>` + `claude mcp add-json -s user <name> '<entry>'` (add-json refuses overwrite); avoid `json.dump` of the 256KB file.
- Lesson: a memory saying "this mechanism does not do what its name says" is a finding to ACT on; Correction 1 sat unused a day while the symptom was live, and the diagnosis was re-derived badly twice.

## A prescribed remedy must not assume its own tool exists

[[prescribed-remedy-must-not-assume-its-own-tool-exists]] (ccmemory v0.13.0 -> v0.13.1)
- `SESSION_PROTOCOL` (SessionStart hook) v0.13.0 said bare: Call `ToolSearch("select:mcp__ccmemory__memory_list")` once. ToolSearch exists only when tool-search is enabled; a model that correctly detected the missing `memory_list` dead-ended hunting for the tool to find a tool. gemma-4-26B session: searched for `ToolSearch`, re-read the list, then made a `Bash` call whose body was its own reasoning as `#` comments.
- Still progress: v0.13.0 targeted SILENT failure (no ccmemory looks like no memory; model concludes falsely "no prior memory"); that closed. Regression: "silently wrong" became "visibly stuck". Don't let one discredit or excuse the other.
- Rule for injected "call tool X": (1) gate: "IF, and only if, X is in your tool list"; (2) state X's absence is NORMAL, say SKIP rather than hunt; (3) forbid the shell fallback by name (no shell path registers an MCP tool); (4) make the terminal state reachable from EVERY branch (v0.13.0's "If still unavailable after that, STOP" hung off the ToolSearch branch). Write for the least capable model that will read it (gemma-4-26B, `instenv.prompt` fan-outs).

## ccenvmcp stdlib MCP shim

[[ccenvmcp-stdlib-mcp-shim]] (2026-06-13)
- `ccenvmcp/`: ~250-line stdlib-only Python 3.9+ FastMCP-compatible shim; the official `mcp` SDK requires >=3.10 in every release, locking out Debian 11/Raspberry Pi OS, RHEL/Alma/Rocky 9, Ubuntu 20.04. `mcp` was the ONLY hard 3.9 blocker (`nats-py` >=3.7, `watchfiles>=0.21` has a 3.9 abi3 build, all packages use `from __future__ import annotations`).
- API `from ccenvmcp import FastMCP`: `FastMCP(name, instructions=...)`, `@app.tool()` sync or async, `@app.tool(name=, description=, schema=)`, `app.run()`, `await app.run_stdio_async()`. Newline-delimited JSON-RPC 2.0 over stdio: initialize / notifications/initialized / tools/list / tools/call / ping.
- 3.9 pitfall (`ccenvmcp/ccenvmcp/transport.py`): never `typing.get_type_hints()` (hints are strings like `"int | None"`, TypeError on 3.9); parse annotation TEXT (`_token_to_schema`) incl. `X | None`/`Optional[X]`/`list[str]`. Tests `ccenvmcp/tests/test_transport.py`.
- Consumers: ccmemory (low-level style, 5 tools, explicit `schema=` to keep the `memory_write` `type` enum; `serve()` split into `build_app()`+`serve()`), ccusage (2 sync tools), ccteam (9 async tools, `instructions=`); `mcp` removed from deps, requires-python >=3.9; versions ccmemory 0.7.0, ccusage 0.2.0, ccteam 0.3.0.
- Installed FIRST by top-level install.sh, deliberately NOT a declared dependency (local source, never PyPI; a declared dep triggers a failing PyPI lookup). See `ccenvmcp/docs/mcp.md`, `ccmemory-debian-build-unknown-install-layout`, `no-per-component-venvs`.

## ccloop CLI flags thread as params

[[ccloop-cli-flags-thread-as-params]] (with `--model`, ccloop 0.9.0 / bundle 0.5.0)
- Runtime config is env-driven (`_config()` reads `CCLOOP_*`) but flags do NOT mutate `os.environ`; they thread as keyword params `cli.main -> runner.cmd_run / cmd_resume -> runner.loop(..., model=None)`; `loop()` does `cfg = _config(); if model: cfg["model"] = model` (flag beats `CCLOOP_MODEL`).
- Recipe for the next value flag (e.g. `--effort`): (1) `cli.py` `_extract_value_flag(argv, flag, what)` generic popper (`--flag=V` / `--flag V`, last wins, missing-value error); `_extract_cutoff`/`_extract_model` wrap it; validate in the wrapper (`--model` rejects empty/whitespace so `--model=` doesn't no-op via the falsy check in `_build_command`). (2) Thread through BOTH `cmd_run` and `--resume-run`'s `cmd_resume`. (3) Update USAGE (Options + env var line "(--x flag wins over this)"). (4) Per-invocation like the env var: resume does NOT remember the model (unlike `--cutoff`, persisted in `<run-dir>/cutoff` because guard/keepgoing hooks in other processes read it).
- Testing: `tests/fake_claude.py` honors `FAKE_ARGS_FILE` (appends argv as JSON lines); assert flag-beats-env: `CCLOOP_MODEL=sonnet`, pass `model="opus"`, `argv[argv.index("--model")+1] == "opus"`. cli parse tests stub `runner.cmd_run`/`cmd_resume` (`_stub_run`/`_stub_resume` in test_cli.py); keep signatures in sync. Never write a seam test whose fake reimplements the override logic; use FAKE_ARGS_FILE end-to-end.
