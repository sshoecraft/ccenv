# ccdirector

A director session — a Claude Code session whose job is project management —
works out a project's goal, writes a work plan, and steers worker sessions
through it until the project's own gate passes. Workers share one working
tree; each takes one item at a time, and an item is done when its check
command passes. Every session is a ccloop run, so each is relayed to a fresh
session when its context fills.

Needs ccenv's `ccloop` and its ccproject tools (`ccproject_criteria.py`,
`ccproject_defects.py`) on PATH; ccenv's `install.sh` installs all three.

```
ccdirector start [PROJECT] [--goal TEXT|FILE] [--workers N]
                 [--cutoff N] [--effort L] [--model M]
                 [--worker-cutoff N] [--worker-effort L] [--worker-model M]
                 [--plan-only] [--replan] [--round-limit N] [--headless]
ccdirector goal [PROJECT]       # the goal-finder alone
ccdirector check [PROJECT]      # the project's gate
ccdirector status [PROJECT]     # items, their states, running workers
```

`start` uses the plan already on file and goes straight to the work. Only
`--replan` writes a new one; a different `--goal` alone keeps the plan.

`wait` and `status` open with a PROGRESS block: items done, criteria green and
how long since one was gained, gate refusals, plan extensions, and what each
worker last said. The director repeats it on its screen after every `wait`.

State lives in the project's `.ccdirector/`. Worker logs are in
`.ccdirector/workers/`.

Design: `docs/ccdirector.md` in ccenv.

Tests: `python3 -m pytest` here. `tests/probe_stale_edit.py` and
`tests/e2e_director.py` run real Claude sessions and are run by hand.
