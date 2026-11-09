# Rules

**These rules are the user's.** Claude does not get to write one — not a new rule, not a step
bolted onto an existing one, not a "note" or "principle" in this preamble that functions as a rule.
Ask, get an explicit yes, then write exactly what was authorized.

**Rules only, paid on every turn.** A line belongs here only if it constrains a turn before any file
is open. Lessons → `.ccmemory`. Design → `docs/`. What changed → `CHANGELOG.md`.

**Every rule is a procedure.** It opens with its trigger — the moment it applies, in words that
match what Claude is about to do — then numbered steps. Each step is one action with one test for
whether it was done, which is also what a hook can check. Lines after the steps are the evidence and
the reason; they are why the rule survives being forgotten and re-derived.

---

## RULE ONE — NEVER ADD A RULE WITHOUT THE USER'S AUTHORIZATION

**Before writing anything into this file:**
1. Show the user the exact text.
2. Wait for an explicit yes. No yes, no write.
3. Write exactly what was authorized and nothing adjacent to it.

A rule Claude wrote for itself outranks nothing and costs a turn on every session thereafter. The
file is small because someone said no to most of what could have gone in it.

## RULE TWO — NEVER RUN `git` UNLESS DIRECTED

*Second on purpose. It is the most dangerous rule here to miss — every other violation in this file
is recoverable.*

**Before any command that contains `git`:**
1. Did the user direct this git command in this turn? If not, do not run it — not to check state,
   not to read history, not "just to see."
2. Is it `restore`, `revert`, `checkout`, `reset` or `stash`? Do not run it, directed or not.

**When a restore looks like the answer:**
1. Stop.
2. Ask the user what they want instead. The answer has never been "run git."

Uncommitted work in the tree is the user's work, and no `git` command that discards it can give it
back.

## RULE THREE — INSPECT WITH THE RIGHT TOOL

**Before drawing any conclusion about what code does:**
1. Read it with the `Read` tool.

**Before any Bash call:**
1. Does it print file contents — `cat`, `sed`, `awk`, `head`, `tail`, a Python one-liner? Use
   `Read` instead.
2. Is it locating — `grep -l`, `grep -n`, counting, field extraction? It may run. Locating is not
   reading.
3. Does it contain `pgrep` or `pkill`, in any form? Do not run it. Use the project's own status
   command, a PID already in hand, `ps -o pid,cmd -C <exact-name>`, or `/proc/<pid>/cmdline`.

`Read` fires the ccmemory `PreToolUse` hook, which injects prior lessons about that path; `sed`
fires nothing. No flag makes `pgrep` acceptable.

## RULE FOUR — DELEGATE CHAINS, NOT CALLS

**Before a Bash call:**
1. Is the next step 3+ shell commands? Hand it to a subagent — `grind` (multi-step shell), `scout`
   (locate things, returns `file:line`), `miner` (parse bulk structured data).
2. Is it a single already-batched command? It stays here.
3. Keep here: the files that must actually be understood, edits, design decisions, project
   judgement.

**While an `Agent` or background task runs:**
1. Do not poll: no `until … sleep`, no `TaskOutput` on a local agent, no watch on
   `tasks/*.output`.
2. Do independent work — read what is needed next, form the next hypothesis, prepare the
   diagnostic.
3. Wait for its completion notification.

**Before ending a turn to wait:**
1. Is `CCLOOP_RUN_ID` set? If not, do not end the turn to wait.
2. Is the work locally live — a `run_in_background` Bash or `Agent` whose `tasks/<id>.output` is
   still held open? If not, do not end the turn to wait.

**Before the session ends:**
1. Check that nothing required is in flight. In-flight agents die with the session.

**When a task exits:**
1. Read its output.
2. Validate it. A task exiting is not a task succeeding.

**Before delegating a task:**
1. Could it violate a rule written this session? Embed that rule verbatim in the agent definition.

**When the `ccloop delegate` hook refuses a Bash call:**
1. Delegate the remainder. Do not retry it by hand.

One `Agent` call costs one request, so it pays only when it replaces several. `TaskOutput` on a
local agent dumps raw JSONL into this context. Work fired on another host is invisible to the Stop
gate: it counts zero, the loop re-feeds "continue", and the session is kicked. Subagents inherit
CLAUDE.md as it was at SESSION START, and `.claude/agents/*.md` is registered then too — verified
2026-08-15: a subagent with zero tool calls quoted the file's opening line and every rule that
existed at session start, and none of those added during it.

*Hook: `ccloop delegate` (`PreToolUse`) nudges at 3 consecutive Bash calls and refuses at 8 inside
a loop run. Being refused means the chain was long enough to drain the budget.*

## RULE FIVE — A SCRIPT IS NEVER A TEMP FILE

**Before writing anything executable:**
1. Check the project's `tools/` and `scripts/` for one that already does the job.
2. Test harness, debug probe or repro case? It goes in `tests/`.
3. Utility, ops or build helper, anything runnable again by hand? It goes in `tools/` or
   `scripts/` — whichever the project has, creating `scripts/` if it has neither.
4. Never `/tmp`, never loose in the project root.

**Before writing to `/tmp`:**
1. Is it data — scratch output, a downloaded blob, a throwaway fixture, an intermediate dump? Only
   then.

`/tmp` is wiped on reboot and the work is gone. "It's just a quick throwaway" is exactly how
reusable work gets deleted — if there is ANY chance it runs a second time, it goes in the repo.

## RULE SIX — EDIT THE FILE, NOT AROUND IT

**Before creating a file that replaces or revises an existing one:**
1. Edit the existing file under its own name. Never `foo_new.py`, never `foo_old.py`, never a
   versioned sibling.
2. If a copy is genuinely needed, name it `foo.py.backup` and nothing else.

**Before editing a Makefile:**
1. Did the user say to in this turn? If not, do not edit it.

## RULE SEVEN — THREE PLACES TO WRITE, AND A THING IN THE WRONG ONE IS LOST

**Before writing to `docs/`:**
1. Would it be wrong next month because the work moved on? Then it does not go in `docs/`.
2. Only design goes there: what the system is, why it is shaped that way, what was decided, what
   is still open. Never status, never history, never a session handoff, never a plan with a date.

**Before adding to the defect queue, where the project has one:**
1. Is it broken and still needing work? Only then.

**When a defect in the queue is fixed:**
1. Remove it from the queue.
2. Write the fix as a `CHANGELOG.md` entry. Nothing in the queue is closed, resolved, pending or
   historical.

**Before recording a lesson:**
1. Write it with `memory_write`. Never hand-edit `MEMORY.md`; it is generated.

**Before writing a `CHANGELOG.md` entry, a `docs/` page, a commit message, a comment, or code:**
1. Does it cite a rule number, or say "per the rules"? Replace it with the reason itself, in that
   artifact's own terms — *why* the version got revved, *what* would have been lost, *what* the
   code must not do.

A citation is a pointer at a file the reader may not have, and it is the first thing to go stale:
renumber or reword one rule and every reference to it in the tree silently becomes wrong, while
reading exactly as authoritative as the day it was written. A design doc holding status rots into a
description of a system that no longer exists, and everyone keeps reading it.

## RULE EIGHT — TROUBLESHOOTING IS A LOOP, NOT A GUESS

**Before any patch, run or experiment while troubleshooting:**
1. Write the hypothesis, and the result that would falsify it.
2. Check the last three runs on this hypothesis. If none cleared the noise, it is disproven: go to
   "When the result disproves the hypothesis."
3. Instrument before changing. Get a live trace.
4. Make one change. Never two at once.
5. Reproduce.

**When the result disproves the hypothesis:**
1. Write a NEW hypothesis. Never patch on a dead one.
2. Return to step 1 above.

**When the result proves the hypothesis:**
1. Patch at the proven cause.
2. Reproduce to confirm.

**When the instrument disagrees with what was expected:**
1. Treat the instrument as right.
2. If it is proven wrong, fix the instrument. Never ignore it.

**Before working around a problem at a higher layer:**
1. Ask. Never do it unasked.

## RULE NINE — WHEN DIRECTED TO COMMIT, COMMIT EVERYTHING

**When directed to commit:**
1. Check `.ccmemory/` for untracked or modified files and stage them first. Only the derived SQLite
   index is ignored.
2. Stage every modified and untracked file in the tree, not only what was edited this session.
3. Skip files already excluded by `.gitignore`.
4. Anything that looks like it holds secrets (`.env`, `credentials.json`, `*.pem`, `*.key`)? Stop,
   surface it, ask. Never skip it silently.
5. Anything else that seems like it should not be committed? Raise it before the commit. Never drop
   it quietly.
6. Write ONE message. If unrelated work is in it, describe both groups honestly.
7. Make one commit. Split only when explicitly asked.

`.ccmemory/` travels with the repo by design — cloning brings it, excluding it loses it on every
other machine. The user's in-progress work — offline edits, another machine, another session — is
part of the project state at that moment; leaving it unstaged assumes Claude knows their commit
boundaries better than they do.

## RULE TEN — NOTHING INDICATES CLAUDE WROTE IT

**Before writing a commit message or PR description:**
1. Write clear, concise bullet points and nothing else.
2. Check: no icons, no graphics, no "Generated with Claude Code", no co-author tag, no
   "<user> and Claude", no generated-by footer of any kind.

## RULE ELEVEN — NO UNDERSCORE PREFIX OR SUFFIX ON A NAME YOU CREATE

**Before introducing a variable or function name into this project's own code:**
1. Does it have a leading or trailing underscore — `_buf`, `buf_`, `_helper()`? Rename it.
2. Exempt only names that are not yours to name: language-mandated (`__init__`, `__repr__`),
   inherited or overridden API names, external interfaces, generated code. **Matching the
   surrounding style is NOT an exemption.**

This is not a Python style opinion and it does not stop at Python. It exists because Claude kept
applying the Python `_private` convention in C++ as well, where it is not a convention at all — the
habit followed the model across languages, so the rule does too. User, 2026-09-02: *"That rule came
about because you kept putting underscore prefixes and postfixes on variable names and functions in
C++ too — not just Python."*

## RULE TWELVE — NAMES AND CLASS SHAPE

**Before writing or changing a class:**
1. Is there an accessor function wrapping a variable? Remove it.
2. Make a class variable private only when nothing outside needs it.
3. Does the name contain "Manager"? Rename it.

## RULE THIRTEEN — NOTHING FAKE, NOTHING DEFERRED

**Before handing work back as finished:**
1. Any TODO or unimplemented path? Finish it.
2. Any mocked, simulated or fabricated data, or a static stand-in "to test with"? Replace it with
   the real thing.
3. Any hardcoded response or canned text where the real path fails? Fix the real path.

**Before reporting a result:**
1. Was it observed? If not, do not present it as observed.

**While troubleshooting:**
1. Fix the actual code. No "simple" replacement function.

This governs what ships and what is claimed, not what is explained. Showing the user an example
command or a snippet in an answer is not a violation; presenting one as a result is.

## RULE FOURTEEN — SPEAK LIKE AN ENGINEER

**Before sending a reply:**
1. Remove platitudes — "You're absolutely right!", "I'm sorry", "Great question!", anything of that
   shape.
2. Leave it direct and technical.

## RULE FIFTEEN — DO NOT STOP AT THE FIRST FAILURE

**When an approach fails:**
1. Take the next evidence-based step.
2. Do not hand back a list of alternatives instead of a solution.

**When genuinely blocked — missing access, missing information, or a decision only the user can
make:**
1. State exactly what the blocker is.
2. State the smallest thing that unblocks it.
3. Do not go pick a different problem. That is a report, not a surrender.

## RULE SIXTEEN — `python3`, ALWAYS

**Before any shell command or script that invokes Python:**
1. Write `python3`, never `python`.

## RULE SEVENTEEN — NEVER RUN A COMMAND THAT CAN TRIGGER A PERMISSION PROMPT

**Before any `rm`:**
1. Does the path contain a variable or glob — `rm -f $D/*`, `rm -rf "$DIR"/*`? Rewrite it: make a
   fresh `mktemp -d` instead of cleaning a directory, or name the file in full with no variable and
   no glob.

**Before running any shell command:**
1. Can Read/Edit/Write/Grep/Glob do it? Use them; they never prompt.
2. Is it in the project's `.claude/settings.json` allowlist? If not, add it BEFORE running it.

The "dangerous rm on a possibly-empty variable path" check is shape-based and fires regardless of
the allowlist; `Bash(rm:*)` does NOT suppress it. In an unattended session a prompt stalls the loop
until a human happens to look.

## RULE EIGHTEEN — `.ccmemory` IS LESSONS, NOT SESSIONS

**Before calling `memory_write`:**
1. Would a future session on a DIFFERENT task be wrong without it — what already bit us, what the
   user corrected, what a turn should have known? If not, do not write it.
2. Will it stop being true when the work moves on? Then it is not a lesson; do not write it.
3. Is it a handoff, status report, plan or change history (`-how-to-continue-it`, `-next-steps`,
   `-progress`)? Do not write it. The transcript is the handoff; point at it.
4. Name it for what it teaches. No session, run or date in the name — not `sess574-…`, not
   `ccloop-<runid>-…`, not `…-2026-09-10`.
5. Put session provenance, when it helps, in the `description`. Never in the `name`.

One project's `.ccmemory` reached 2801 files and 44 MB this way, 38% of them named for a ccloop run
and session number, while the durable material — `trap-`, `technique-`, `feedback-` — came to 97
files.

## RULE NINETEEN — ONE VERSION PER PIECE OF WORK

**Before the first change to the project's source in a session:**
1. Read the top entry of `CHANGELOG.md`. It names the open version and the work it is for. If there
   is no `CHANGELOG.md`, the first bump creates it.
2. Has that version been closed — committed and pushed, installed, or the owner said so? Then this
   change opens a new version, even for the same work.

**Before every change to the project's source:**
1. Is it part of the open version's work? Join that version; do not bump. A new session is not new
   work, and neither is a new bug found during it.
2. Is it for different work? Bump the version immediately before making the change.
3. At the bump, write the new `CHANGELOG.md` entry naming the work — "mpath", "DRBD", "the
   installer" — never the defect, fix or test failure.

**Before changing the version number inside an open version:**
1. Is it to raise its size — patch to minor, minor to major? Only then. Never to add a step.

A write to `.ccmemory/` is not a change to the source and never bumps the version.

Once-per-session counted context windows, not work: ccloop restarts a session every time one fills,
and 0.90.31 became 0.90.122 in twelve days with six releases among them. Before that, one
uninterrupted session revved seven times before a single release. Once-per-entry then counted bugs:
16-node mpath testing opened 0.90.123 and 0.90.124 for two hangs it found, because the entry named
a defect and the next defect did not match it. The bump cannot wait for the release, because Claude
never sees one: installs run as another user, or from someone else's clone, and nothing an install
does is allowed to write back into the source tree.
