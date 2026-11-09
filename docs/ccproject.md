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
- **Committed files:** the store carries its own `.gitignore` for the locks and
  for temp files, so only the data is committed.
- **Files elsewhere in the repo:** `.ccproject/config.json` may name another path
  for either file (`"store"`), relative to the project root. It must resolve
  inside the project, which keeps the reason XDG was rejected: the state still
  travels with the code. This exists for a project whose own tooling already
  reads its files where they are. Moving the files would mean editing every one
  of those readers at once. Pointing ccproject at them changes nothing for the
  project.

## A project can say ccproject is not its tool

A project can keep its release state in tools of its own. If it does, the
skill still tells every session to use ccproject, and that causes two failures:
- **The answer is empty or stale.** A ccproject command gives an answer that
  looks authoritative but comes from a store the project's tools never write.
- **A stale copy appears.** A well-meant `import` creates a second store, a
  snapshot frozen at the moment of the import. It reads as current until
  someone notices it is not.

`"external": {"criteria": "...", "defects": "..."}` in `.ccproject/config.json`
names the project's own tool for each. The ccproject tool for that part then
refuses to run in the project, `import` included, and says which tool to use.

**Why refuse instead of forwarding.** Forwarding would make `ccproject_criteria.py`
behave like a different program in one project, while every document describes
it the same way. That is the shadowing the driver design forbids. A refusal
costs the session one call, and afterwards it knows which tool is correct.

When the board's defect queue is external, the board leaves out its "no
blocking defects" line, because that queue is not ccproject's to grade. To
switch a project over, delete the line from `config.json`.

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
  - FAIL when any value has a failing status (`"failing"` on the board, default
    `FAIL`)
  - PARTIAL otherwise
- **Why not PARTIAL on a failure:** smartbot's own tool marked a row PARTIAL even
  when a cell failed. A row with a failing cell has failed, and saying PARTIAL
  hides that. The same argument covers any status a project counts as a
  failure. In mxfs, a run that died mid-test is INCOMPLETE and may have wedged
  the node, so `failing` lets the board say FAIL there as well.
- **One value at a time:** the board takes `--cell VALUE` and grades each row at
  that value alone, leaving out the rows that do not apply there. A requirement
  that holds on one configuration says nothing about another, so the question a
  release asks is per value.
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
supplies hooks:
- **Grading:** `applies`, `grade` and `roll_up`.
- **Results:** `before_result` and `on_result`.
- **Defects:** `blocks`, `unrated`, `on_write` and `on_remove`.
- **Parsers:** `options` and `commands`.

A hook that is missing, or that returns None, means the generic behaviour. So a
driver overrides only what its project actually does differently.

- **Hooks on writes are before the save:** `before_result` runs before the
  result is checked, so a project can record a status the generic list does not
  know, or turn an over-budget PASS into a FAIL, instead of having the result
  refused. `on_result`, `on_write` and `on_remove` run with the lock held and
  before the save. A hook that exits refuses the write and leaves the file as
  it was. If a hook ran after the save, a refusal would come too late, and a
  project's own records (a run ledger, retired runs) could fall behind the
  file.
- **Read-only driver commands:** a driver subcommand that only reports declares
  `driver_readonly` and runs without the lock, the way the board does. A long
  report must not hold off a harness that is recording results.

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

Each mutating command holds an exclusive lock on the file it writes, from load to
save. If the lock covered only the save, two concurrent writers could each read
the same old state, and the later save would silently undo the earlier one. Saves
write a temp file and rename it over the old one, so a reader without the lock
(the board reading the queue, for instance) always sees a whole file. A lock
still held after 30 seconds is reported as wedged rather than waited on.

The lock is `.<file>.lock`, in the same directory as the file. There used to be
one lock, `.ccproject/.lock`, for both files. That lock only excluded ccproject
writing alongside itself. A project whose own tools write the same file (mxfs's
harness writes its board while sessions use ccproject) needs both writers to
take the same lock, or each can save over the other. A name derived from the
file gives the project's tool a lock it can take.

## The two tools are separate files

Each script is self-contained: standard library only, with no import from one to
the other. They are installed by copying them into the skill directory and
linking them into `~/.local/bin`, with no package to build. The price is a small
duplication:
- the lock, save and lookup helpers
- the defect-gate rule, which the criteria script reads to build its verdict line

The two copies of that rule must stay in agreement. The tests check each tool's
gate behaviour on the same filters.
