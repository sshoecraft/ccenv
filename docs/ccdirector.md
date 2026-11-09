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
workers do the work; the user sets the goal and answers what only the user
can.

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
`goal-done` (the gate passes) or `goal-stuck REASON` (nothing can move
without the user) ends the steering run; workers are stopped; ccdirector
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
`.ccproject/config.json` can keep its files anywhere in the tree (mxfs keeps
them under `data/`), so whether there is a board is the tool's answer
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
criterion is not green, recording what is broken as defects; break the work
into items one worker can finish and check, each with a command that decides
it; group them by section; prioritise (data loss, crashes and safety first,
then what others depend on, then what turns the most criteria green, then
cheap fixes that remove noise); order what conflicts with dependencies and
interface items; and say honestly how much can run at once.

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
- `items`, each with:
  - `id`, `what`, `serves` (criteria), `priority` (1 most urgent), `size`;
  - `check`: a shell command run from the project root that exits 0 when the
    item is done, and `check_timeout` (1–540 s, default 300);
  - `section`: the area of the project, so a worker keeps to one area;
  - `depends_on`: items that must be done first — also how the director keeps
    apart items that touch different files but conflict anyway;
  - `resources`: things besides files needing exclusive use (test machines,
    rigs); two items naming one never run at once;
  - `interface`: an item changing something many items use; it runs only
    while nothing else is in progress, and nothing else starts until it is
    done;
  - `paths`: where the work is expected to land — guidance for the worker,
    not a lock;
- `risks`.

Items are not assigned to workers in the plan. The plan says what the work is
and what may run together; workers take items while the work runs.

Validation refuses: missing or mistyped fields, a missing check or one allowed
longer than 540 s (a worker runs it inside one Bash call, which Claude Code
caps at 10 minutes), unknown dependencies, dependency cycles, and an item
serving a criterion the plan does not list.

Earlier versions gave each worker exclusive ownership of paths. That was
dropped: in a project like mxfs nearly every defect touches the same core
sources, so file ownership serialises all the work onto one worker. What two
workers in one file actually risk is handled by the edit lock, Claude Code's
own stale-read check, dependencies and interface items (see Edits in one
tree).

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
2. It acts: an item done whose criteria are still red gets new items; an
   item failing its check repeatedly is split or corrected; a parked item is
   answered by resubmitting it revised; a failing worker's log is read.
3. `plan submit` is the only lever. Resubmitting merges into the work state:
   new items start pending; a revised parked or done item goes back to
   pending; a revised item in progress stays with its worker, and a dropped
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

An item that cannot be finished is handled at the item's level. A worker asking an
advisor about its item is troubleshooting; the director asking one is choosing a
direction, so the two are counted apart and neither satisfies the other. After 3
failed checks in a row ccdirector parks the item, so a worker cannot grind on it
forever. An item parked 3 times with nobody asking for help makes its next worker
consult, and `item done` is refused until the answer is back. Any consult by the
worker holding the item resets its count, which is why a worker that already asked,
as a project's own rules may tell it to, pays for no second call. Nothing here gives
up: the director can still revise or drop the item, or stop if it is truly blocked.

By default a run has no round limit: it exists to keep trying until the gate
passes. `--round-limit N` ends it after N rounds with no criterion gained, and
`goal-done` then ends the run the way `goal-stuck` does. A round counts
once however often `goal-done` is retried, and needs the plan to have changed, so
starting again on the same plan allows one more round rather than ending at once.

The director cannot declare the goal met. `goal-done` runs the project's gate
and writes ccloop's marker only if it passes. `goal-stuck REASON` records the
reason in `stuck.json` and ends the run, and is refused while anything is in
progress or can be handed out.

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
- `--worker-cutoff`, `--worker-effort`, `--worker-model`, separate from the
  director's `--cutoff`, `--effort`, `--model`.

A worker loops on `ccdirector item next --wait` (an item, `WAIT`, or `NONE`),
does the item, and reports with `item done ID`, which runs the item's check:
pass marks it done, fail returns the output and the worker keeps it. A worker
that cannot finish an item parks it with a reason. `NONE` writes the worker's
ccloop marker, so the worker's run ends when nothing is left for it, instead
of idling. A worker that exits gives back any item it still held; the
supervisor launches another when work appears. Three workers in a row that
exit nonzero within a minute stop further launches, with a
`workers_failing` event for the director.

An item is ready when it is pending, its dependencies are done, no item in
progress holds a resource it names, and no interface item is in progress.
Ready items go out by priority, then the worker's own section, then id.

The work state is `.ccdirector/state.json` (per item: status pending,
claimed, done or parked; holder; attempts; last check output) and
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
- **Interface changes** (a shared struct, a signature, a locking rule):
  interface items run alone.
- **A broken tree in the middle of another worker's change.** The worker
  instructions say a build or test failure may be someone else's unfinished
  change, to check before chasing it, and to park only if it persists.
- **Edits through Bash** (`sed -i`, redirects, formatters) bypass the hook and
  Claude Code's check. The worker instructions forbid them; nothing enforces
  it.
- **Changes correct alone and wrong together** are caught by item checks and
  the gate on the combined tree, which the director then investigates.

### ccteam

ccdirector began inside ccteam, to use ccteam's lock layer for worker file
ownership, and left once ownership was dropped: it uses nothing of ccteam.
ccteam's lock reaches its DLM through a per-session MCP server whose IPC
socket every session in a project shares (the last to start takes it), and
its hook lets the edit through when no server answers. Workers are on one
machine in one tree, which a lease file covers without a server.

The two cannot run in the same sessions. ccteam's plugin loads in every
Claude Code session, so it would run in the director's and every worker's:
in its NATS mode each session's watcher publishes edits and applies the other
sessions' edits to its own copy of the tree, which here is the same copy, so
every edit would be replayed onto a tree that already has it; its hook would
take a second lock around every edit through a server that cannot tell the
sessions apart, and tell the model to `force_claim` when it refuses.
`ccdirector start` therefore refuses to run while ccteam is configured — an
MCP server named `ccteam` (user scope, the project's entry in
`~/.claude.json`, or the project's `.mcp.json`), an installed or enabled
plugin whose name contains `ccteam`, or a hook running `ccteam` — and names
what it found. ccenv's installer removes ccteam as a retired component.

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
- Resources are exclusive between items ccdirector hands out; nothing stops a
  worker using a machine its item does not name.
- Per-worker accounts (`CLAUDE_CODE_OAUTH_TOKEN` per worker) are not wired:
  every worker runs on the account of the user who started ccdirector.
- Function-level claims (the first edit to a function claiming it until the
  item is done) would stop two workers reworking one function, which
  dependencies only prevent when the director foresees it.
- Builds from a snapshot of the tree, so another worker's half-finished change
  cannot break a running build.
- Work whose proof takes longer than 540 s has no item check of its own; the
  long run belongs to a criterion's detector.
- Workers run headless; there is no view of a worker but its log.
- Anthropic's terms for sessions run with nobody attached.
