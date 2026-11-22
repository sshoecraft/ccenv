# ccdirector

A director session — a Claude Code session whose job is project management —
works out a project's goal, writes a work plan, and steers worker sessions
through it until the project's own gate passes. The director splits the goal
into items that are goals of their own and can be worked side by side. Workers
share one working tree; each owns its item from start to finish and keeps it
until the item's check command passes, then takes the next. Every session is a
ccloop run, so each is relayed to a fresh session when its context fills.

Needs ccenv's `ccloop` and its ccproject tools (`ccproject_criteria.py`,
`ccproject_defects.py`) on PATH; ccenv's `install.sh` installs all three.

```
ccdirector [--cutoff N] [--effort L] [--model M]
           start [PROJECT] [--goal TEXT|FILE] [--workers N]
                 [--cutoff N] [--effort L] [--model M]
                 [--worker-cutoff N] [--worker-effort L] [--worker-model M]
                 [--worker-stall MIN] [--plan-only] [--replan] [--round-limit N] [--headless]
ccdirector goal [PROJECT]       # the goal-finder alone
ccdirector check [PROJECT]      # the project's gate
ccdirector status [PROJECT]     # items, their states, running workers
```

`--cutoff`, `--effort` and `--model` set the director session's. They go before
or after `start`; given in both places, the one after `start` wins. Workers take
theirs from `--worker-*`. Before `goal`, `--model` sets the goal-finder's.

Workers that die are relaunched. A worker that dies within a minute of starting
is relaunched after a wait that doubles up to 15 minutes, and the retries never
stop. Three such deaths in a row (an API or network outage) mean one worker is
tried at a time until one stays up, and then all come back. A worker whose log
is silent for `--worker-stall` minutes (default 30, four times that while a tool
call is open; 0 turns it off) is ended as hung and relaunched. A worker waiting
inside one of its ccdirector tools is not hung, however long it waits.

Shared resources (a test rig, a VM pool, a device) are declared once in the
plan, each by a short id with a capacity. Every worker gets its own MCP server
(`ccdirector mcp`) whose tools block at no cost until their answer is ready:
`item_next` (the next item), `item_done` (runs the item's check to its end,
however long), `acquire` (units of a resource, in order, with a request that
fits going ahead of a larger one that does not), `release`, `run` (a command of any length, returning its exit code and
output tail) and `resources` (holders and waiters). Workers therefore wait in
line for the rig instead of polling or re-feeding their session.

`start` uses the plan already on file and goes straight to the work. Only
`--replan` writes a new one; a different `--goal` alone keeps the plan.

`wait` and `status` open with a PROGRESS block: items done, criteria green and
how long since one was gained, gate refusals, plan extensions, and what each
worker last said. The director repeats it on its screen after every `wait`.

State lives in the project's `.ccdirector/`. Worker logs are in
`.ccdirector/workers/`.

Design: `docs/ccdirector.md` in ccenv.

Tests: `python3 -m pytest` here. `tests/probe_stale_edit.py`,
`tests/e2e_director.py` and `tests/live_mcp_worker.py` run real Claude sessions
and are run by hand.
