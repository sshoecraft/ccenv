---
name: prospect-integrate
description: >
  Wire ccprospect (prospective memory: the PROSPECT INBOX + intention/forecast
  contracts) into the CURRENT project's binding surface so its agent loop or
  interactive sessions actually answer the inbox instead of merely seeing it.
  Use this skill whenever the user says "integrate prospect", "prospect
  integrate", "wire up prospective memory", "add ccprospect to this project",
  "bootstrap prospect", or asks how to make an agent/loop act on fired
  prospects. One-time per project (re-running refreshes the managed block in
  place). It classifies the project (interactive / ccloop / custom-loop),
  lands a marker-fenced integration block in the right file — asking, never
  guessing, for custom loops — and records the decision in
  .ccprospect/integration.json so re-runs are deterministic.
---

# Integrate ccprospect into this project

ccprospect ships the MECHANISM (inbox, immutable contracts, creation gate,
SessionStart injection). Injection is not obedience: how strongly the inbox
binds is a per-project prompt decision. This skill lands that binding in the
right place for the project's shape. It follows the proven prompting laws
from the design archive: enforcement must be its OWN new step with a forced
written artifact and NOT-DONE grammar — prose that models fluently agree
with gets ignored.

## Step 0 — preconditions

1. `command -v ccprospect` must succeed and `ccprospect status` must show
   `registered: True`. If not, stop and tell the user to run ccenv's
   `./install.sh` (or `ccprospect install`) first.
2. Run `ccprospect where` to confirm the startup dir is the project the user
   means. The store anchors to the session's startup CWD — in multi-account /
   multi-run-dir projects, each run dir gets its OWN `.ccprospect/` store
   automatically; the BINDING (this skill's output) is per project and only
   needs to land once.
3. Keep the two artifacts distinct when the project's agents run OUTSIDE the
   source repo (custom loops with per-user run dirs): this skill's products —
   the binding block/diff and `integration.json` — belong in the SOURCE repo,
   run once, by whoever edits the source. The RUNTIME stores self-create
   later, in each agent's run dir, on that agent's first `prospect_file`
   (reads never create; an empty-store `prospect_inbox()` returns
   `pending_count: 0`, so the inbox step completes trivially until then). Do
   not pre-create run-dir stores, and do not run this skill per user — the
   per-user step is only ccenv's `./install.sh` (tools, hooks, and this skill
   are all user-scoped).

## Step 1 — honor a prior integration

If `.ccprospect/integration.json` exists, this project was integrated before.
Read it, use its recorded `shape` and `binding_file`, refresh the managed
block in place (Step 3), and skip discovery. Do not re-ask what is recorded.

## Step 2 — classify the project (deterministic tree, in this order)

**(a) Custom-loop project** — the project has its own agent runner with its
own system prompt / constitution (indicators: project docs or ccmemory
mention a "constitution", a prompt build, an agent loop that isn't ccloop;
or the user says so). Procedure, strictly:

  - Do NOT guess the target file. Locate the loop's prompt artifact by
    reading the project's CLAUDE.md, docs/, and searching its ccmemory
    (`memory_search("constitution")`, `memory_search("system prompt")`).
  - Present what you found and the proposed step block (Step 3, loop
    variant) to the user, and ask them to confirm the target file before
    touching anything.
  - If the target is a BUILT/generated artifact (a constitution assembled
    from source fragments): never edit the built file. Produce the edit as
    a diff against the SOURCE fragments, in the constitution's own step
    grammar and numbering, and hand it to the user — constitution deploys
    into live agents are owner-run.
  - Adapt the step wording to the project's domain vocabulary (its loop's
    names for reconcile/survey/act), but PRESERVE invariantly: own new
    step, forced row artifact, NOT-DONE grammar, `pending_count == 0`
    completion, NO_CONTRACT always legal.

**(b) ccloop project** — `.ccloop/` exists, or the user runs the loop via
`ccloop "<criteria>" "<task>"`. The strongest binding surface is the
criteria file (it is re-fed to the model verbatim at every stop-hook gate).

  - Find the criteria file (conventionally a `criteria.md` / `crit.md` the
    user passes via `$(cat ...)`). If you cannot identify it, ask the user
    which file they pass to ccloop. If they pass a literal string instead
    of a file, recommend moving it to a file so this block can live there.
  - Land the loop variant block (Step 3) in the criteria file, AND the
    interactive variant in the project's CLAUDE.md (relayed sessions read
    CLAUDE.md; the criteria gate re-asserts the step).

**(c) Interactive project** — everything else. Land the interactive variant
in the project's `CLAUDE.md` (create the file if absent).

## Step 3 — land the managed block (idempotent)

Fence the block with these exact markers so re-runs replace it in place —
if the markers already exist in the target file, REPLACE everything between
them instead of appending:

```
<!-- [CCPROSPECT INTEGRATION] managed by prospect-integrate; edit via the skill -->
...block...
<!-- [/CCPROSPECT INTEGRATION] -->
```

**Interactive variant** (CLAUDE.md):

```markdown
## Prospective memory (ccprospect)

- A `PROSPECT INBOX` may be injected at session start: fired/due items are
  mail from prior sessions. For EACH one, read its intention and submit one
  disposition: `prospect_ack(id, disposition)` — done | keep |
  defer(+next_review) | cancel_attention(+note) | resolve(+hit|miss|
  unresolvable). `prospect_file` REFUSES new prospects while fired items sit
  unacknowledged.
- Before wrapping up work that expects something to happen LATER ("when X
  ships", "when this file changes", "re-check Monday"), file it with teeth:
  `prospect_file(title, intention, predicate, expires[, expect, bucket,
  evidence])`. Declining to file is always fine — never file filler.
- Contracts are immutable: revise with `prospect_amend` (the original still
  resolves counterfactually). `prospect_report()` is the factual record.
```

**Loop variant** (criteria file / constitution source; renumber to fit the
loop's own step grammar):

```markdown
### STEP P1 — RESOLVE THE PROSPECTIVE INBOX (every cycle, immediately after reconcile/startup)

Call `prospect_inbox()`.

For EVERY returned row, submit exactly one disposition via `prospect_ack`:

`id | disposition | note-or-evidence`

Allowed dispositions: `done | keep | defer(+next_review) |
cancel_attention(+note, required) | resolve(+hit|miss|unresolvable)`.

A fired or due row without a submitted disposition = step NOT DONE. This
step is complete ONLY when `prospect_inbox()` returns `pending_count = 0`.
`prospect_file` will mechanically REFUSE new contracts until it does.

### STEP P2 — FORM OR DECLINE FORWARD EXPECTATIONS (every cycle, after the survey/scan step)

Consider up to 3 candidates for forward expectations. Write one row per
candidate considered:

`target | evidence-as-of | predicate type + numbers | expires | action`

`action` must be `CREATE` or `NO_CONTRACT`. `CREATE` is valid only after
`prospect_file` returns an id — an intention without a returned id is not
CREATE. `NO_CONTRACT` is always legal: never invent a contract to fill a
slot. A row without a typed predicate, numeric level(s) where the type
takes them, and an expiry does not count as considered.
```

Notes to apply while landing:
- Keep the interactive variant SHORT — the SessionStart hook already
  injects the full protocol every session; CLAUDE.md only needs the
  standing obligations.
- For tight-cadence loops, remind the user that `cmd_*` probes rate-limit
  via per-contract `min_interval` (default 3600s) — contracts meant to be
  checked every cycle need `min_interval` set low deliberately.

## Step 4 — record the decision (ask once, persist forever)

Write `.ccprospect/integration.json` (create `.ccprospect/` if needed —
this file is NOT gitignored; it is a project fact and travels with the
repo):

```json
{
  "shape": "interactive | ccloop | custom",
  "binding_file": "<repo-relative path the block landed in (or the constitution SOURCE the diff targets)>",
  "notes": "<anything a future re-run must know, e.g. 'constitution is built; edit fragments under prompts/'>",
  "integrated_at": "<ISO-8601>"
}
```

If the project uses ccmemory, also `memory_write` a short project note
naming the binding surface, so future sessions don't re-derive it.

## Step 5 — verify and report

1. `ccprospect inbox` runs clean (empty store is fine — reads never create).
2. Show the user the landed block (or the constitution diff) and where.
3. Do NOT file a demo/test prospect — that is filler, and filler is exactly
   what the caps exist to prevent. The first real prospect will create the
   store.
4. Tell the user the one thing the tool cannot do for them: for custom
   loops, the block only binds once THEY deploy the constitution version
   containing it.
