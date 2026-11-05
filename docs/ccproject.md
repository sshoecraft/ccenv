# ccproject: defect queue and criteria board

`ccproject_defects.py` and `ccproject_criteria.py` hold a project's release state:
- what is broken (the defect queue)
- what must be true before the project ships (the criteria board)

They are generic. A project's own rules plug in through a driver instead of being
built into the tools. `ccproject/README.md` is the user guide. This page records
why the tools are shaped the way they are.

## Release readiness is one verdict

Every project has a set of things that must hold before it is ready to release.
"No open blocking defect" is one of them: the defect queue is the worklist
behind that single criterion. The board treats it that way. When a project keeps
a queue, the board prints "no blocking defects" as a line of its own and counts
it in the VERDICT. `ccproject_criteria.py --gate` is therefore the whole release
check.

A project whose queue is only a record, not a release bar, sets
`"defects_gate": false` on its board.

## Where the data lives

- **Location:** `.ccproject/defects.json` and `.ccproject/criteria.json` in the
  project. The data travels with the repository, the same way `.ccmemory/` does,
  so a clone has the project's release state.
- **Why not XDG:** a per-user XDG directory was considered and rejected. It
  would separate the state from the code it describes, and leave it out of every
  other machine and every other clone.
- **Finding the project:** each tool walks up from the cwd to the nearest
  directory that already holds a `.ccproject/`. If there is none, it creates
  `.ccproject/` in the cwd on the first write. Walking only to an existing store
  means a command run from a subdirectory reaches the project's files instead of
  starting a second store there.
- **Committed files:** the store carries its own `.gitignore` for the lock and
  for temp files, so only the data is committed.

## Shapes and compatibility

Both files keep fields the tools don't know about, on every record and at the top
level. That is what lets an existing project's own `defects.json` or
`criteria.json` import unchanged. It was verified against smartbot's and mxfs's
real files: smartbot's queue imports byte-identical, and every mxfs record
survives a write.

The shapes are the ones those projects already used:
- **Defects:** `id, severity, opened, updated, summary, evidence, next`.
  Severity is ranked `critical, high, major, medium, minor`.
- **Criteria:** `id, requirement, detector, why, phase, status, measured, window,
  recorded`.

`"project"` is reserved at every level of both files (board, row, cell, queue,
defect). The tools never read or rewrite it, and no generic field will ever take
that name. That gives project data a place that cannot collide with a field the
tools add later.

## Defects have no status field

A defect is open from `add` until `remove`. `remove` takes `--why` and
optionally `--fixed` or `--disproved`, and prints the line to paste into the
CHANGELOG. A queue with a status field turns into an archive. Closed entries pile
up, every reader has to filter them out, and "is this still a problem?" becomes a
field to keep in step with reality. Here, being in the queue is the answer to
that question.

## The defect gate

By default every defect blocks. Two filters on defect fields narrow that, and
they treat a missing field differently on purpose:

- **`gate_exclude {field: values}`:** a match does not block, and a missing field
  still blocks. Excluding is a claim that a defect does not matter, so a record
  nobody has classified must not slide out of the gate.
- **`gate_require {field: values}`:** a record blocks only with a listed value, so
  a missing field does not block. Requiring a rating says unrated records are
  not yet release blockers. The gate prints a NOTE with their count, because
  passing them silently would hide work.

mxfs's release rule, `gate-require impact integrity stability` plus
`gate-exclude scope harness`, reproduces the blocker set of mxfs's own tool
exactly, including its count of unrated records.

## Matrix criteria

Some criteria are not one answer:
- **smartbot:** a requirement can hold for a warrior and fail for a mage.
- **mxfs:** a requirement can hold on one configuration and fail on another.

A board can therefore declare one axis (`axis class warrior mage …`). A row on
that axis keeps one cell per value under `per_<axis>`, which is exactly the map
those projects already wrote (`per_class`, `per_config`).

- **Row status:**
  - PASS when every required value is green
  - FAIL when any value is FAIL
  - PARTIAL otherwise
- **Why not PARTIAL on a failure:** smartbot's own tool marked a row PARTIAL even
  when a cell failed. A row with a failing cell has failed, and saying PARTIAL
  hides that.
- **Becoming a matrix row:** a row turns into a matrix row on its first per-value
  write, so a harness writing `--class mage` is never refused.
- **Spelling:** `--cell` is also accepted as `--at` (mxfs's spelling) and as the
  axis name (`--class`, smartbot's spelling), so existing harness calls keep their
  shape.

## Staleness is by build, not by age

A green result recorded on a different build than the one being released says
nothing about that build. That is how smartbot's own board defines staleness:
"A green earned before a change landed is not evidence about the build after
it." An age limit was considered and rejected. It would mark a still-valid
result stale only because time passed, while keeping a result from an older
build green.

Results can carry `--build`. `--build B` grades against B: any green from
another build, or with no build recorded, reads STALE. The stored result is never
rewritten. Grading only changes how the board reads it.

## Harness logic lives in drivers

The two projects these tools replace each built project-specific machinery into
their own copies:
- **mxfs:** a 19,000-line run ledger, settle notes, a passes-required rule, and
  a configuration grammar that decides which configurations a row covers
- **smartbot:** per-class grading

Building any of that into the generic tools would make them a merge of every
project's harness. Each project would carry the others' rules, and the generic
behaviour would grow with every new project.

Instead a project adds `.ccproject/driver.py`. Both tools load it, and it
supplies hooks: `applies`, `grade`, `on_result`, `blocks`, `options` and
`commands`. A hook that is missing, or that returns None, means the generic
behaviour. So a driver overrides only what its project actually does differently.

- **Imported, not run as a subprocess:** grading mxfs's board means about 2,000
  row-by-configuration judgements, and a subprocess per call would make every
  board print take seconds. Import is also standard library and costs nothing per
  call.
- **Subcommands:** driver subcommands hang off the same two commands
  (`ccproject_criteria.py incidents`), so a project's tooling does not drift into
  a second script.
- **The API version:** a driver declares `API = 1`, and a driver for any other
  version is refused. If the hook signatures change, an old driver cannot keep
  running and quietly grade the board wrong.
- **No shadowing:** a driver may add flags and subcommands but never replace a
  built-in one. A replaced command would behave differently in one project while
  reading the same in every doc.
- **Trust:** the driver is project code that runs whenever either tool runs in
  that tree. That is the same trust as running the project's own tests.
- **When logic moves into the core:** when a second project needs the same driver
  logic, that logic becomes generic.

## Locking and writes

Each mutating command holds an exclusive lock, `.ccproject/.lock`, from load to
save. If the lock covered only the save, two concurrent writers could each read
the same old state, and the later save would silently undo the earlier one. Saves
write a temp file and rename it over the old one, so a reader without the lock
(the board reading the queue, for instance) always sees a whole file. A lock
still held after 30 seconds is reported as wedged rather than waited on.

## The two tools are separate files

Each script is self-contained: standard library only, with no import from one to
the other. They are installed by copying them into the skill directory and
linking them into `~/.local/bin`, with no package to build. The price is a small
duplication:
- the lock, save and lookup helpers
- the defect-gate rule, which the criteria script reads to build its verdict line

The two copies of that rule must stay in agreement. The tests check each tool's
gate behaviour on the same filters.
