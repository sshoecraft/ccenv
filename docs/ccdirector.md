# ccdirector

## Problem

Several Claude Code sessions working the same project converge on the same
work. Each decides for itself what to pick up next, and nothing stops two of
them editing the same file. Claude Code's cross-session messaging lets them
talk, but an agreement between sessions ("I'll stay out of src/dlm/") holds
only while both models remember it.

Long sessions have a second problem. When a session's context fills, Claude
Code compacts it: the model writes a lossy summary of its own history at the
point its context is fullest, and constraints and settled decisions drop out.
ccloop answers this for one session by relaying to a fresh session pointed at
the full transcript on disk. Nothing answers it for a group of sessions that
must stay coordinated across relays.

## Shape

The director is a Claude Code session whose system prompt makes it a project
manager. It does no project work. It determines what the goal is and what done
means, breaks the work into items, sets priorities and dependencies, and then
steers the work until the project's own gate passes. Workers are ordinary
Claude Code sessions, each relayed by ccloop, that take one item at a time,
all in one shared working tree.

`ccdirector` is the program around them. It owns everything that must not
depend on a model remembering to do it: resolving the goal, launching and
watching the sessions, handing out items, refusing a malformed plan, deciding
whether an item is done (its check) and whether the goal is met (the gate),
and serialising writes to a file.

Judgement is the director's; mechanics and verdicts are ccdirector's; the
workers do the work; the user sets the goal. Nobody answers questions during a
run: the director makes every call itself and records it.

```
ccdirector start [--goal TEXT|FILE] [--workers N] [PROJECT]
  ├─ --goal given ──► the goal is the directive
  └─ no goal ──► goal-finder: one read-only `claude -p`
                   ├─ found ──► the goal
                   └─ not found ──► exit nonzero with the reason
  ▼
PLANNING run: the director (a ccloop run) writes the plan;
              `ccdirector plan submit` validates it, `plan done` ends the run
  ▼  (--plan-only stops here)
STEERING run: the director loops on `ccdirector wait`, revising the plan
  ║    while ccdirector's supervisor keeps up to N workers running:
  ║    each takes the next ready item, works, `item done` runs its check
  ▼
`goal-done` (the gate passes) or `goal-stuck --probe CMD REASON` (a
dependency is down: CMD fails) ends the steering run; workers are stopped; ccdirector
reports and exits 0 (gate passes) or 1
```

## What defines done

A project's definition of done lives in its own records, kept by ccenv's
ccproject tools in `.ccproject/` (design: ccenv `docs/ccproject.md`):

- the criteria board, `ccproject_criteria.py`: what must be true, each with a
  detector that measures it;
- the defect queue, `ccproject_defects.py`: what is known broken.

The queue counts as one criterion on the board ("no blocking defects"), so
`ccproject_criteria.py --gate` is the whole release check. A project with a
queue but no board is judged by `ccproject_defects.py --gate`; the criteria gate
is never met with no rows.

ccdirector never reads the board or the queue itself. The tools own the
format, apply the project's driver (`.ccproject/driver.py`) and decide what
blocks; a second reader would drift from them. A project's
`.ccproject/config.json` can keep its files anywhere in the tree (under
`data/`, for example), so whether there is a board is the tool's answer
(`--json` lists rows), never a file test. The one exception: an emptied queue
and no queue look the same through the tool, so ccdirector locates the queue
file the way the tools do and tests only that it exists.

**Done is checked, not claimed.** `ccdirector check` runs the project's gate:
exit 0 done, 1 not done, 2 nothing defines done. A model saying it is finished
counts for nothing; ccloop's own design records that a model can write `DONE`
without finishing.

## Finding the goal

With `--goal`, the goal is the directive (text, or a file holding it). Without
one, ccdirector runs the goal-finder before anything else starts.

The goal-finder works down a fixed order and stops at the first source that
gives a goal:

1. **The criteria board.** Done is already defined; the goal is to make the
   gate pass.
2. **The defect queue, with no board.** The goal is to clear what blocks.
3. **The project itself.** README, docs, CLAUDE.md, awareness docs,
   `.ccmemory/`, CHANGELOG, roadmap files, build and test entry points. From
   these it states what the project is meant to do and what is missing, and
   proposes the criteria that would define done, each with a detector. The
   CHANGELOG's top entry records what someone last directed a session to work
   on: evidence of work in flight, not an authority on the goal.
4. **Nothing.** It says so, with what it looked at. ccdirector exits nonzero
   and nothing else starts. A wrong goal costs more than no goal.

ccdirector reads the board and queue through their tools first and puts them in
the goal-finder's prompt (as the tools' own text views, capped), so the
goal-finder needs only Read, Grep and Glob.

### The call

One `claude -p` per run, built on the adapter pattern from the user's other
projects (builder → llm-bench → warrior, and aitrader's auditor):

- `--output-format json --json-schema SCHEMA`; the answer is read from
  `structured_output`.
- `--tools Read,Grep,Glob`. `--tools` limits which tools exist at all, so
  nothing the user's settings allow can be reached.
- `--system-prompt-file prompts/goalfinder.md`, the prompt on stdin (out of
  `ps`, clear of the argv limit), `--strict-mcp-config`,
  `--no-session-persistence`, CLAUDECODE removed from the child's environment
  (a nested CLI refuses to start under one).
- Run in the project root, because it has to read the project. warrior's
  grader runs in an empty temp directory only because it needs no files.
- Not `--bare`: it accepts only `ANTHROPIC_API_KEY` auth and skips the
  logged-in account.

Every way it can fail ends `ccdirector` with a nonzero exit and the reason,
never with a goal: a usage-limit notice (status 429, recognised by the phrases
builder collected, or `api_error_status`), any other `is_error`, a nonzero
exit, a timeout, a missing `structured_output`.

### Checking the answer

The CLI validates against the schema; ccdirector then re-checks every field,
because a string "false" read as a bool once executed a trade a reviewer had
rejected (warrior). It also checks the answer against the project's records:

- `source: board` or `queue` with no board or queue is refused;
- `criteria.existing` must name ids that are on the board;
- proposed criterion ids must be new, and usable as ids;
- `source: scan` must propose at least one criterion, or nothing would define
  done;
- not found must come with source `none` and a reason.

The resolved goal is kept in `.ccdirector/goal.json`.

## The director session

ccdirector starts the director as a ccloop run in the project root, ccloop
unmodified. Everything passes through ccloop's existing configuration:

| What | How |
|---|---|
| System prompt (`prompts/director.md`) | `--system-prompt-file` via `CCLOOP_CLAUDE_EXTRA_ARGS`, as aitrader's agent-run does. It replaces Claude Code's default system prompt; ccloop appends its own with `--append-system-prompt-file`. |
| No project edits | One deny rule, `Edit(//<root>/**)`, as `--settings` JSON via `CCLOOP_SETTINGS`. Claude Code checks every file-editing tool (Edit, Write, NotebookEdit) against `Edit` rules, so one rule covers all of them; `Write(...)` and `NotebookEdit(...)` rules are never consulted and draw a "never matches" warning at startup. Deny rules hold in every permission mode, including the `bypassPermissions` ccloop uses. `//` starts an absolute path in a rule; `/x` would be relative to the settings source. |
| Relay cutoff | `--cutoff N` (before or after `start`) passes ccloop's `--cutoff`, in thousands of tokens, on the start and on every resume; 0 relays only when the context window fills. Without it ccloop's default applies. |
| Effort and model | `--effort` and `--model` pass ccloop's `--effort` and `--model` for the director; the workers' are `--worker-effort` and `--worker-model`. |
| Role | `CCDIRECTOR_ROLE=director` and `CCDIRECTOR_ROOT` in the environment. |
| The goal | ccloop's task text: which run it is, the goal, its source, the criteria it rests on, criteria to add to the board, the worker count, and the gate command; the steering run's also carries the state of every item. |
| When a run is finished | ccloop's criteria. Planning: ccdirector has accepted the plan, and it covers every criterion of the goal that is not green; the text says this is the planning run's exit only, not progress on the board, because "criteria met" otherwise reads as the board's criteria being met. Steering: `goal-done` confirmed the gate passes, or `goal-stuck` recorded why nothing can progress. |
| Signalling finished | `ccdirector plan done`, `goal-done` and `goal-stuck` write ccloop's `criteria-met` marker. The marker is under the project root, where the deny rule blocks the director's own writes (Write and Bash redirection alike), so ccloop's usual `echo YES` cannot work. `plan done` refuses unless `CCLOOP_RUN_ID` names a run folder and the stored plan still validates and was submitted for the current goal, so a plan for an earlier goal cannot end the run. |

The prompt path must contain no whitespace, because ccloop splits
`CCLOOP_CLAUDE_EXTRA_ARGS` on whitespace. ccdirector refuses to run rather
than overwrite a `CCLOOP_SETTINGS` the user already set.

ccloop prints its run id only to stderr, which the interactive TUI shares, so
ccdirector finds the run afterwards by a marker line in its `task.md`.

### What the director does

The prompt walks it through: repair a plan on file rather than starting over;
add the proposed criteria to the board, so the board, not the
director's memory, is the definition of done; find out why each not-green
criterion is not green, recording what is broken as defects; split the goal
into items that are goals of their own, each one a worker can carry from start
to finish, each with a command that proves it; group them by section;
prioritise (data loss, crashes and safety first, then what others depend on,
then what turns the most criteria green, then cheap fixes that remove noise);
order what conflicts with dependencies; and aim for at least as many items
ready at once as there are workers, saying so in `risks` when the goal cannot
be split that far.

#### Items are goals, not steps

The director is the captain of a fleet, not the navigator of each ship. It
sets destinations (items), watches the weather (the board, the gate, what
workers report) and changes a destination when the weather calls for it. Each
worker plots and sails its own course to its destination: it takes an item and
works it the way a single session would — read, change, test, read the
result, change again — until the item's check passes, and only then takes
another.

This is the reason for running several workers at all. Two plain sessions,
each told to work on one of two unrelated problems, finish about twice as fast
as one. ccdirector must do at least that with N workers on N independent
items, and it can only if an item stays with its worker. Earlier versions let
items shrink to steps: checks were capped at 540 s, so they could prove only a
step; three failed checks took the item away; workers parked items back to
the director to wait for results; an item that named a resource held all of
it, and interface items stopped every worker. Each hand-off made a fresh
worker relearn the code (about 390k tokens to start, in one project) and
waited on the director (a median of 55 minutes per round trip). In that
project four workers ran at about 1.2 times one session, at about twice the
tokens per defect.

Bash stays available for read-only inspection and for the ccproject and
ccdirector commands. The deny rule covers Edit, Write and NotebookEdit; a
Bash command that writes files is not covered: no tool-level rule can see
which files a shell command will write. The prompt forbids it.

### The plan

The director writes nothing with Edit/Write. It submits JSON on stdin to
`ccdirector plan submit`, which validates it and writes
`.ccdirector/plan.json` and a readable `plan.md`, atomically, or refuses
it with every problem listed so the director can fix them in one pass. A
refused plan never replaces an accepted one.

A plan is an asset of the project, and once written it is reused. `ccdirector
start` with a usable plan on file skips both the goal-finder and the planning
run and goes straight to the workers and the steering run; exiting and starting
again continues the same plan. Only `--replan` writes a new one, and it moves
the old plan to `plan.json.backup` and `plan.md.backup` first so nothing can
mistake it for the new one. A different `--goal` is not a request to replan:
the plan is kept and stderr says so. The one case that opens a planning run on
an existing plan is a plan that no longer validates; that run repairs the
listed problems and keeps everything else.

`plan submit` records on the plan the goal `ccdirector start` was running
(`written_for`), because the director words the plan's own `goal` itself. A
reused plan runs under that recorded goal, and `plan done` refuses a plan not
submitted for the goal of the current run.

The director never asks the user a question or waits for a decision. Nobody is
at the terminal, and a run waiting on an answer makes no progress. It makes the
call and records it in the plan's `risks`, where the user can read it. To review
a plan before any worker runs, use `--plan-only`; a later `start` uses that plan.

The plan holds only what is the director's. The definition of done and the
known work stay on the project's board and queue.

- `goal`, `source`, `gate`, `criteria` (board ids the goal rests on);
- `resources`: the registry of everything workers share — machines, rigs,
  devices, pools — each declared once by a short id with a `capacity` (units
  usable at once) and a `what` (see Shared resources);
- `items`, each a goal one worker owns from start to finish, with:
  - `id`, `what`, `serves` (criteria), `priority` (1 most urgent), `size`;
  - `check`: a shell command run from the project root that exits 0 when the
    item's goal is met. It proves the goal, however long that takes; workers
    run it through their `item_done` tool, which has no time limit.
    `check_timeout` (seconds) is optional; 0 or absent means no limit;
  - `section`: the area of the project, so a worker keeps to one area;
  - `depends_on`: items that must be done first — also how the director keeps
    apart items that touch different files but conflict anyway, and how a
    shared change (a struct, a signature, a locking rule) goes first, as an
    item of its own;
  - `paths`: where the work is expected to land — guidance for the worker,
    not a lock;
- `risks`.

Items are not assigned to workers in the plan. The plan says what the goals
are and what depends on what; workers take items while the work runs, and
`plan submit` ends with a `PARALLEL` line: how many items can be worked right
now against the number of workers, and, when fewer, a reminder to split
further or say in `risks` why the goal cannot be split. It never refuses on
that count, because a refusal would push the director to invent independence.

Validation refuses: missing or mistyped fields, a missing check, a
`check_timeout` that is not a whole number of seconds of 0 or more, unknown
dependencies, dependency cycles, an item serving a criterion the plan does not
list, a registry id that is not 1–40 characters of `a-z 0-9 . _ -` or a
capacity below 1, an item that names `resources`, and an item with
`interface: true`. The last two were how an item held a resource or the whole
tree for its whole run; with goal-sized items that run for hours, both
serialised everything near them. A plan on file that still has either fails
validation, which sends it back to the director to repair. Resources used to
be free text; in one project the director described the same VM pool in a
slightly different sentence on every item, and since resources compared as
text, nothing ever waited for the pool. Resources are now named only in the
registry and by workers' `acquire` calls.

Earlier versions gave each worker exclusive ownership of paths. That was
dropped: in a project where nearly every defect touches the same core sources,
file ownership serialises all the work onto one worker. What two workers in
one file actually risk is handled by the edit lock, Claude Code's own
stale-read check and dependencies (see Edits in one tree).

### When a run ends

ccdirector runs the director twice: a PLANNING run (exit: an accepted plan,
signalled with `ccdirector plan done`) and a STEERING run (exit:
`ccdirector goal-done` or `goal-stuck`). Separate runs give each its own
ccloop criteria; the director continues from the plan and the work state on
disk, not from memory, so nothing is lost between them.

If a run ends without doing what it is for, ccdirector reopens it: it removes
ccloop's `criteria-met` marker and puts the problems at the top of the run's
`resume.md`, which ccloop places in the next session's prompt. The summarizer
rewrites `resume.md` after that session, so the feedback is seen once, which
is all it is for. After three attempts, or if ccloop itself aborted (any other
nonzero exit), ccdirector stops the workers, reports and exits 1.

The user ending the run outranks all of that. Answering `n` at ccloop's
relaunch prompt exits ccloop 3 and Ctrl-C exits 130; on either, ccdirector
reopens nothing, starts no later stage even if the plan on file is valid, stops
any workers, and exits with that status. ccloop keeps exit 0 for a run that
finished, because a wrapper cannot tell a user's stop from convergence
otherwise.

## Steering

The steering director loops:

1. `ccdirector wait` blocks (up to 540 s, inside one Bash call) until there
   are events it has not seen, then prints them with every item's state. It
   returns at once when every item is done or the work is stalled.
2. It acts: an item done whose criteria are still red gets new items; idle
   workers get new independent goals; a parked item (its worker says the goal
   itself is wrong) is answered by resubmitting it revised; a check that
   cannot pass however good the work is gets fixed; a failing worker's log is
   read. An item in progress is left to its worker unless new information
   changes its goal.
3. `plan submit` is the only lever. Resubmitting merges into the work state:
   new items start pending; an item is revised when its `what` or `check`
   changes, and a revised parked, waiting or done item goes back to pending (a
   change to any other field — priority, paths, size, section, dependencies,
   a field the plan format dropped — is bookkeeping and reopens nothing: when
   every field counted, two plan-format repairs that only removed item resource
   fields reopened every finished item, 122 of them in one project); a revised item in progress stays with its worker, and a dropped
   item is dropped; either way the worker holding it is told on its next tool
   call by a PreToolUse hook, because a worker reads its item once and can then
   work for hours on text the director has withdrawn.

The director's screen is the only one the user watches, and workers run
headless, so `wait` and `status` open with a PROGRESS block and the director
repeats it as plain text after every `wait`; the screen collapses tool output.
The block sets items done against criteria green, because an item's check only
proves a piece of work happened, while the board says whether the result is good
enough. It also gives how long since a criterion was gained (a gain is a new
best, so a criterion flapping red and green does not count), gate refusals, plan
extensions, and each worker's last sentence and how long since its log moved.

After two gate refusals with no criterion gained, the block adds `LIKELY
LOOPING`. It is a warning and stops nothing. The count is deliberately of whole
rounds that moved nothing, not of attempts: a worker retrying one item any
number of times never touches it, and any gain resets it.

A PostToolUse hook on the `ask_*` advisors, in the director and every worker,
records each consult, because those tools run outside anything ccdirector
launches. After two consults with no criterion gained the block adds `STUCK`,
and the prompt tells the director that its next item must differ in kind rather
than be another variant. That too only warns: a hard problem can take more
advice than that before it moves.

A round ends when every item is done and the gate refuses. That is the point
where the plan has been carried out and shown not to be enough, so it is a
checkpoint rather than a cue to append the next idea: the director reworks the
plan around what the evidence says is wrong. Advisor calls are expensive, so a
consult is required only on every third replan, and any consult resets that
count. `plan submit` enforces it, because a prompt instruction alone is only as
reliable as the model; `--no-consult REASON` keeps a project with no advisor from
deadlocking, and records why. This is the director reworking its own plan, with
finished items kept; `--replan` is the user discarding it.

A consult has to be answered, not merely asked. Claude Code moves a call still
running after two minutes to the background and hands control back, and
`PostToolUse` fires at that handoff, so the hook records such a call as pending.
It counts once the transcript shows its `<task-notification>` delivered to the
session, not merely queued, since only then can the director have read it. `plan
submit` is refused while one of the director's consults is pending. A pending
consult is dropped after 30 minutes or at the next `ccdirector start`, because
the task does not survive its session and nothing else would clear it.

A hard item stays with its worker. A failed check returns its output and the
worker keeps going; nothing takes the item away. After 3 failed checks in a row
the worker must ask an advisor before its next check (`item_done` is refused
until the answer is back), so it does not grind on one idea, and then it
carries on with the item. Any consult by the worker holding the item resets
the count, which is why a worker that already asked, as a project's own rules
may tell it to, pays for no second call. A worker asking an advisor about its
item is troubleshooting; the director asking one is choosing a direction, so
the two are counted apart and neither satisfies the other. Only the worker
gives an item back, and only for weather: the goal itself is wrong or cannot be
done as written (parked, for the director to revise), or it needs another item
done first (waiting, see Waiting on another item). Nothing here gives up: the
director can still revise or drop the item, or stop if it is truly blocked.

By default a run has no round limit: it exists to keep trying until the gate
passes. `--round-limit N` ends it after N rounds with no criterion gained, and
`goal-done` then ends the run the way `goal-stuck` does. A round counts
once however often `goal-done` is retried, and needs the plan to have changed, so
starting again on the same plan allows one more round rather than ending at once.

The director cannot declare the goal met. `goal-done` runs the project's gate
and writes ccloop's marker only if it passes. `goal-stuck --probe CMD REASON`
is the only other way out, for one case: a dependency the work already uses
(a machine, a gateway, a credential, a data feed) is down. CMD is a command that
succeeds when the dependency works; ccdirector runs it in the project root
(60 s limit, a timeout counts as down) and refuses while it succeeds, so the
stop rests on a measurement, not the director's judgement. A probe that fails
by construction (`false`, `exit 1`) is refused. On a stop, `stuck.json` holds
the reason, the probe and its output. It is also refused while anything is in
progress or can be handed out.

Judgement-based stops were removed on purpose. A director ended a run with
every criterion still open to more work, because an advisor saw no lever left,
it wanted a paper run approved, it wanted a bar changed, and it wanted data the
project never had. Each of those is the director's call to make or work to
plan, so none ends a run.

## Workers

The supervisor, a thread in `ccdirector start`, keeps as many workers running
as there is work for — items in progress plus items ready to hand out, up to
`--workers`. Each worker is one headless ccloop run in the project root,
output to `.ccdirector/workers/<name>.log`, launched with:

- its instructions (`prompts/worker.md`) as the ccloop task, and "`ccdirector
  item next` printed NONE" as its criteria;
- `CCLOOP_SETTINGS`: deny rules on `.ccdirector/**` and `.ccloop/**`, and the
  edit-lock hooks;
- `CCDIRECTOR_ROLE=worker`, `CCDIRECTOR_WORKER=worker-N`, `CCDIRECTOR_ROOT`;
- its own MCP server, `ccdirector mcp`, through `--mcp-config
  .ccdirector/workers/worker-N.mcp.json`, passed to claude by ccloop's
  `CCLOOP_CLAUDE_EXTRA_ARGS` (see Waiting at no cost);
- `--worker-cutoff`, `--worker-effort`, `--worker-model`, separate from the
  director's `--cutoff`, `--effort`, `--model`.

A worker loops on the `item_next` tool (an item, or `NONE`), carries the item
to done, and reports with the `item_done` tool, which runs the item's check to
its end however long it takes (`ccdirector item done ID` in Bash runs the same
check but ends at Claude Code's 10-minute Bash limit): pass marks it done, fail
returns the output and the worker keeps it. The worker waits for its own runs
and tests, including ones queued on the project's run queue, with the `run`
tool, which blocks at no cost. `NONE` comes only when every item in the plan
is done; it writes the worker's ccloop marker and the run ends.
Short of that — items in progress, parked items the director has yet to revise,
waiting items, a plan being reworked — `item_next` keeps waiting,
which costs nothing, and the worker takes the next item in the same session with
its context intact. Ending the run whenever nothing was momentarily ready cost a
fresh session per item: in one project, 122 worker sessions in 46 hours, each
paying about 390k tokens of cache creation to start, which made a defect cost
about twice what it cost in a single plain session. A worker that exits gives
back any item it still held.

### Waiting on another item

A goal can turn out to need another item done first: a dependency the plan
missed. Its worker parks it until that item is done, `item park ID --after
OTHER --reason TEXT [--hours H]`, or, for something outside the plan, until a
condition, `--until CMD`, a quick command that succeeds once what it waits for
is there. The item is then `waiting`, not `parked`:

- It is not the director's to answer. The worker takes other work.
- The pool's second thread checks every waiting item once a minute: `--after`
  from the work state, `--until` by running the command, side by side, outside
  the work lock, each run limited to 30 s. When one is free the item returns
  to pending at once. Among items of equal priority, the worker that parked it
  is first in line, since it already has the context. `item_next` hands it
  over with `RESUMED`, what it waited for, and the worker's reason.
- After H hours (24 by default) without it coming free, the item is parked for
  the director, with a reason that starts "waited since".
- A director revision of a waiting item returns it to pending and ends the
  wait.

`park --after` refuses an unknown item, the item itself and an item already
done. `park --until` runs the condition once before accepting it, and refuses
one that already succeeds, one the shell cannot run (exit 126 or 127), which
would never succeed, and one that runs past 30 s, because that is a wait, not
a test. While any item waits, the work is not stalled: `goal-stuck` is refused,
and `wait` does not report `STALLED`.

A worker does not park to wait for its own results. Its item is its goal, and
waiting for the goal's test is part of doing it, as in a single session; it
waits with `run`, which costs nothing. Parking to wait sent each result
through the director (a median of 55 minutes in one project) and back to a
worker that had to relearn the item.

### The worker pool

`ccdirector start` holds a pool of `--workers` slots, `worker-1` to
`worker-N`, and keeps as many running as there is work for: items in progress
plus items ready to hand out. Each slot is idle, starting, running or backing
off. Its state, quick-failure count, relaunch count and retry time are in
`state.json` under `workers`, and the pool's own outage state is under `pool`.

- **Worker ended.** When a worker exits, its item goes back and its slot is
  free. The pool relaunches into the slot as soon as there is work.
- **Quick death.** A worker that exits nonzero within 60 seconds of starting
  never got going. Its slot backs off 15 seconds, doubling each time up to 15
  minutes, before it relaunches. No count ever stops the retries. A worker that
  stays up past 60 seconds, or exits 0, resets the count.
- **Outage.** Three quick deaths in a row across the pool point to a shared
  cause: the API, the network, a credential. That raises `workers_failing`, and
  the pool then tries one worker at a time on the same doubling wait. The first
  worker that stays up raises `workers_resumed`, clears every slot's backoff
  and brings the pool back to full strength. Whether the run needs a dependency
  that is down is still decided only by `goal-stuck --probe`. The pool never
  ends a run.
- **Hung worker.** A worker whose log has been silent for `--worker-stall`
  minutes (default 30; 0 turns this off) is hung, and this raises
  `worker_stalled`. While its log shows an open tool call (`→ Tool` with no
  `ok`/`ERROR` result after it), the limit is four times longer. That covers
  advisor consults, subagents and a blocking `item next --wait`. A worker
  blocked in one of its ccdirector tools (in line for the rig, inside a long
  `run`) writes nothing to its log, but its MCP server touches
  `workers/worker-N.beat` every minute, and the pool counts that as activity:
  however long it waits, it is not hung. A worker that is waiting on background
  work through Bash is never this quiet either: ccloop's Stop hook
  re-feeds it every few minutes, and each re-feed writes to its log. The pool
  ends the worker's whole process tree, SIGTERM then SIGKILL 20 seconds later.
  It walks `/proc` for this, because ccloop starts claude in a session of its
  own, outside the worker's process group. The slot then backs off as after a
  quick death.
- **Frozen MCP server.** Each worker's MCP server touches
  `workers/worker-N.alive` every minute from its event loop, its only thread.
  Once it has come up in the current launch, an alive file that has not moved
  for 10 minutes means the server has stopped answering: every ccdirector tool
  of that worker is dead, however busy its log looks. The pool ends and
  relaunches the worker as for a hung one, also under `--worker-stall 0`. In
  one project a server sat for hours in uninterruptible sleep inside an NFS
  open on `.ccdirector/`; Claude Code aborted its `acquire`, then an instant
  `resources` call, after 30 minutes of silence each, and the worker went on
  without its tools. A server in that state cannot be killed until the file
  operation returns, but the relaunched worker gets a new one. The alive file
  is separate from the beat file because the beat file means "waiting inside
  a tool" to the hung-worker check; a server touching that file all the time
  would hide a hung claude.

PROGRESS shows a `pool:` line for slots that are starting or backing off and
the relaunch count. During an outage it adds a `WORKERS PAUSED` line naming
the next try. The director's instructions say none of these needs a restart
or is a reason to stop.

An item is ready when it is pending and its dependencies are done. Ready items
go out by priority, then to the worker that parked the item (it has the
context), then by the worker's own section, then id.

### Waiting at no cost

A worker that waits through Bash pays for every minute of it. A Bash call ends
within 10 minutes, so a long rig run becomes a background command, and from
then on ccloop's Stop hook re-feeds the session — a full request with the whole
context — every time its in-hook sleep runs out. Measured in one project: 3,632 of
19,054 worker requests (19%) were such re-feeds, plus poll loops and `sleep`s.

An MCP tool call that does not return keeps the session suspended in place, no
turns and no tokens, for as long as it takes (scheduler-mcp is built on the same
fact). So each worker gets `ccdirector mcp` (`mcpserver.py`), whose tools block:

| Tool | Returns when |
|---|---|
| `item_next` | an item is ready for this worker, or every item is done (`NONE` ends its run) |
| `item_done(id)` | the item's check ends, however long it takes: PASSED, FAILED (the item stays with the worker) or REFUSED, with the output tail and the full log under `.ccdirector/runs/` |
| `acquire(resource, units, why)` | the units are granted; returns a lease |
| `release(lease)` | at once |
| `run(command, timeout_minutes)` | the command ends: exit code, seconds, output tail, full log under `.ccdirector/runs/` |
| `resources` | at once: units in use, holders, waiters |

Three Claude Code limits apply to a long MCP call. A call silent for 1800 s is
aborted, so every blocking tool sends a progress notification each 60 s (Claude
Code sends a `progressToken` with each call, and progress resets that limit). A
per-server `"timeout"` in the MCP config is not a way to allow longer waits: it
is a hard wall-clock limit that progress does not reset, measured with
`tests/probe_mcp_timeout.py`, so ccdirector sets none. An
interactive session moves a call over 2 minutes to the background; workers are
headless (`-p`), which is exempt. The wall-clock limit (`MCP_TOOL_TIMEOUT`) is
about 28 hours. `acquire`, `run` and `item_done` are cancellable: a cancelled
or abandoned `acquire` leaves the queue, and a cancelled `run` or `item_done`
ends the command's whole process tree; a cancelled check records no result.

The server is built on the ccenvmcp shim, which runs each call in its own task
(so a wait never stalls pings or other calls), hands a `ctx` to tools that ask
for progress, and stops only tools registered as cancellable.

### Shared resources

The plan's registry declares each shared resource with a capacity. A worker
takes units only while it runs on the resource — `acquire`, run, `release` —
and keeps editing and thinking without holding anything. Requests that do not
fit wait in one queue per resource, in order, with one exception: a request that
fits in the free units goes ahead of earlier ones that do not (backfill), and of
several that fit the earliest goes first. Strict order left a 3-unit request
idle behind a whole-pool request with 30 of 32 units free, in one project, while
the whole-pool request itself waited for a 2-hour run to end. So that a
whole-pool request is not put off forever by a stream of small ones, nothing
passes the head of the queue once it has waited 2 hours: from then on the units
drain to it. No item holds a
resource for its whole run: workers on different goals share a pool unit by
unit, and a check that needs a resource runs while its worker holds a lease.

Leases and the queue are in `state.json` (`leases`, `queue`), changed under the
same POSIX lock as everything else. A lease records the worker's claude process
(the MCP server's parent, pinned by pid and start time): when that process is
gone the lease is dropped at the next `acquire` by anyone, and the pool drops a
worker's leases and queue places when it sees the worker exit. A waiting
`acquire` asks again every 2 seconds and each ask stamps its queue place; a
place not asked about for 2 minutes belongs to a call that is gone without
withdrawing (a frozen server, a call dropped without a cancel) while its claude
lives on, and it is dropped with an `abandoned` line in `resources.log`. Left
in line it would never take its units, and every request it fits ahead of
would wait for it forever. `ccdirector
start` clears both, since the holders died with the earlier run. Routine
acquires and releases go to `resources.log`, not `events.jsonl`, so they never
wake the director; PROGRESS and `status` show a line per resource in use.

The work state is `.ccdirector/state.json` (per item: status pending,
claimed, done, parked or waiting; holder; attempts; last check output; for a
waiting item its condition, since when and its deadline) and
`events.jsonl`, both changed only under one POSIX lock (`work.lock`). Items
still marked in progress when `ccdirector start` begins are returned: the
workers that held them died with the earlier ccdirector.

## Edits in one tree

Workers share the tree and its files. What can go wrong, and what handles it:

- **Lost updates.** Claude Code refuses a Write to a file changed since the
  session read it, and applies an Edit (an exact-string replacement) on top of
  the other change with a note that the file holds changes not in the
  session's context (`tests/probe_stale_edit.py`, measured on 2.1.319 with a
  second session and with an outside process). That check-then-write is not
  atomic across processes, so each worker's PreToolUse hook takes the file's
  lease and PostToolUse releases it: one write to a file at a time, held for
  one tool call, never for a task. A lease is an O_EXCL file in
  `.ccdirector/locks/`; one older than 20 s is abandoned (a failed tool
  call never reaches PostToolUse) and broken; a write waits up to 45 s, then
  is refused with the holder's name. The hook fails closed: any error refuses
  the edit. Every lease operation is logged to `locks.log`.
- **Two workers reworking the same code.** Dependencies and sections: the
  director orders items that change the same logic, and keeps a section's
  items with one worker where it can.
- **Shared changes** (a struct, a signature, a locking rule): an item of
  their own that the items needing them list in `depends_on`.
- **A broken tree in the middle of another worker's change.** The worker
  instructions say a build or test failure may be someone else's unfinished
  change, to check before chasing it, and to give it a few minutes before
  running again.
- **Edits through Bash** (`sed -i`, redirects, formatters) bypass the hook and
  Claude Code's check. The worker instructions forbid them; nothing enforces
  it.
- **Changes correct alone and wrong together** are caught by item checks and
  the gate on the combined tree, which the director then investigates.

## Alternatives evaluated

### Claude Code agent teams

The closest built-in feature: a lead session spawns teammates, keeps a shared
task list, and messages teammates by name. It does not fit: the team lives
inside the lead's session (one team per session, the lead cannot be replaced,
resuming does not restore in-process teammates), so the lead cannot be relayed
without losing its team; teammates compact rather than relay; there is no
documented way to give a teammate its own credentials; file conflicts are
handled by advice only.

### awslabs/cli-agent-orchestrator

Drives each agent in a tmux window by pasting text and reading the screen; its
open issues are mostly that approach's fragility. It unsets all `CLAUDE*`
variables at launch, removing per-agent credentials, and has no file ownership.
Taken from it: the supervisor dispatches and then ends its turn rather than
waiting.

### claude-coordinator

Isolates workers with a git worktree each and resolves collisions at merge
time; everything else is advisory. Taken from it: launch-time
`Edit(//<abs>/**)` deny rules as an enforcement layer.

### Git worktrees

Rejected. They move conflicts to merge time and put git in the workers' hands.
With writes serialised and stale reads refused in one shared tree, workers see
each other's changes as they land instead of at a merge.

### Compaction instead of relays

Rejected for long runs. Anthropic's own guidance says a fresh session carries
"zero carried-over rot" and that the model is at its least capable when
compacting; user reports describe dropped constraints after compaction.

## Open questions

- Whether a second `--append-system-prompt-file` would let the director keep
  Claude Code's default system prompt instead of replacing it.
- Nothing stops a worker using a shared resource without acquiring it; the
  instructions say to, and the project's own locks still sit underneath.
- An item's check that uses a shared resource relies on its worker holding a
  lease while it calls `item_done`; nothing enforces it.
- Whether directors split goals finely enough to keep every worker busy on a
  real project, and whether N workers then reach about N times one session's
  rate there: the `PARALLEL` line and the `WORKERS IDLE` warning say when they
  do not, but a measured run is still owed.
- The broker is one host's: leases live in the project's `state.json` under a
  POSIX lock, which is only safe while every worker runs on the machine that
  runs `ccdirector start`.
- Per-worker accounts (`CLAUDE_CODE_OAUTH_TOKEN` per worker) are not wired:
  every worker runs on the account of the user who started ccdirector.
- Function-level claims (the first edit to a function claiming it until the
  item is done) would stop two workers reworking one function, which
  dependencies only prevent when the director foresees it.
- Builds from a snapshot of the tree, so another worker's half-finished change
  cannot break a running build.
- Workers run headless; there is no view of a worker but its log.
- Anthropic's terms for sessions run with nobody attached.
