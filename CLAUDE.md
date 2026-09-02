# Rules

**These rules are the user's.** Claude does not get to write one — not a new rule, not a bullet
bolted onto an existing one, not a "note" or "principle" in this preamble that functions as a rule.
Ask, get an explicit yes, then write exactly what was authorized.

**Rules only, paid on every turn.** A line belongs here only if it constrains a turn before any file
is open and is a one-line imperative. Lessons → `.ccmemory`. Design → `docs/`. What changed →
`CHANGELOG.md`.

**One decision per bullet.** A bullet states a single thing to do or not do, with a single test for
whether it was violated — which is also what a hook can check. Bullets without an imperative are the
evidence and the reason; they are why the rule survives being forgotten and re-derived.

---

## RULE ONE — NEVER ADD A RULE WITHOUT THE USER'S AUTHORIZATION

- Ask before writing any rule into this file. An explicit yes, then exactly what was authorized and
  nothing adjacent to it.
- A rule Claude wrote for itself outranks nothing and costs a turn on every session thereafter. The
  file is small because someone said no to most of what could have gone in it.

## RULE TWO — NEVER RUN `git` UNLESS DIRECTED

*Second on purpose. It is the most dangerous rule here to miss — every other violation in this file
is recoverable.*

- No `git` command and no subcommand unless the user says so in that turn. Not to check state, not
  to read history, not "just to see."
- Never `restore`, `revert`, `checkout`, `reset` or `stash`. Uncommitted work in the tree is the
  user's work, and no `git` command that discards it can give it back.
- If a restore looks like the answer, that is the moment to stop and ask what they want instead. The
  answer has never been "run git."

## RULE THREE — INSPECT WITH THE RIGHT TOOL

- Every conclusion about what code does comes from the `Read` tool. It fires the ccmemory
  `PreToolUse` hook, which injects prior lessons about that path; `sed` fires nothing.
- Bash search may LOCATE — `grep -l`, `grep -n`, counting, field extraction. Locating is not
  reading. `cat`, `sed`, `awk`, `head`, `tail` and a Python one-liner are not reading, whatever they
  print.
- Never run `pgrep` or `pkill`, in any form. No flag makes it acceptable. Use the project's own
  status command, a PID already in hand, `ps -o pid,cmd -C <exact-name>`, or `/proc/<pid>/cmdline`.

## RULE FOUR — DELEGATE CHAINS, NOT CALLS

- When the next step is 3+ shell commands, hand it to a subagent — `grind` (multi-step shell),
  `scout` (locate things, returns `file:line`), `miner` (parse bulk structured data). One `Agent`
  call costs one request, so it pays only when it replaces several; a single already-batched command
  stays put. What stays here: the files that must actually be understood, edits, design decisions,
  project judgement.
- Never poll and never block on background work. No `until … sleep`, no `TaskOutput` on a local
  agent (it dumps raw JSONL into this context), no watch on `tasks/*.output`. An `Agent` delivers
  its own completion — that is the sanctioned way to wait. While one runs, do independent work: read
  what is needed next, form the next hypothesis, prepare the diagnostic.
- Ending the turn to wait requires `CCLOOP_RUN_ID` set AND the work locally live — a
  `run_in_background` Bash or `Agent` whose `tasks/<id>.output` is still held open. Work fired on
  another host is invisible to the Stop gate: it counts zero, the loop re-feeds "continue", and the
  session is kicked.
- Nothing required is in flight when a session ends — in-flight agents die with it. A task exiting
  is not a task succeeding: read and validate its output.
- *Hook: `ccloop delegate` (`PreToolUse`) nudges at 3 consecutive Bash calls and refuses at 8 inside
  a loop run. Being refused means the chain was long enough to drain the budget — delegate the
  remainder, do not retry it by hand.*

## RULE FIVE — A SCRIPT IS NEVER A TEMP FILE

- `/tmp` holds data, never code. Scratch output, downloaded blobs, throwaway fixtures, intermediate
  dumps — genuinely transient, regenerable, never read again.
- Anything executable goes in the repo: test harnesses, debug probes and repro cases in `tests/`;
  utilities, ops and build helpers, anything runnable again by hand, in `tools/` or `scripts/` —
  whichever the project has, creating `scripts/` if it has neither. Never loose in the project root.
- Before writing a new script, check the project's `tools/` and `scripts/` for one that already does
  the job.
- `/tmp` is wiped on reboot and the work is gone. "It's just a quick throwaway" is exactly how
  reusable work gets deleted — if there is ANY chance it runs a second time, it goes in the repo.

## RULE SIX — EDIT THE FILE, NOT AROUND IT

- Keep the same filename. Never `foo_new.py`, never `foo_old.py`, never a versioned sibling. If a
  copy is genuinely needed it is `foo.py.backup` and nothing else.
- Never edit the Makefile unless told to in that turn.

## RULE SEVEN — FOUR PLACES TO WRITE, AND A THING IN THE WRONG ONE IS LOST

- `docs/` IS DESIGN, AND NOTHING ELSE. What the system is, why it is shaped that way, what was
  decided, what is still open. Never status, never history, never a session handoff, never a plan
  with a date on it. **The test: if it would be wrong next month because the work moved on, it is
  not a design document.**
- `CHANGELOG.md` IS WHAT CHANGED IN THE SOURCE. Newest first, every entry headed with the date and
  the version — `## 2026-09-02 — 1.1.0`. What changed and why, in prose. Nothing else goes in this
  file.
- The version is revved as part of the change, not after: patch for a fix, minor for a feature,
  major for a break.
- THE DEFECT QUEUE IS WHAT IS BROKEN AND STILL NEEDS WORK, where the project has one. A defect that
  is fixed is REMOVED, and the fix is a `CHANGELOG.md` entry. It is a work queue, never an archive:
  nothing in it is closed, resolved, pending or historical, because those entries would not still be
  in it.
- `.ccmemory` IS THE LESSONS — what already bit us, what the user corrected, what a turn should have
  known. Write it with `memory_write`; `MEMORY.md` is generated and hand-editing it is blocked.
- NEVER CITE A RULE OUTSIDE THIS FILE. No rule number, no "per the rules", in a `CHANGELOG.md`
  entry, in `docs/`, in a commit message, in a comment, or anywhere in the code. Say the reason
  itself, in that artifact's own terms — *why* the version got revved, *what* would have been lost,
  *what* the code must not do. A citation is a pointer at a file the reader may not have, and it is
  the first thing to go stale: renumber or reword one rule and every reference to it in the tree
  silently becomes wrong, while reading exactly as authoritative as the day it was written.
- A design doc holding status rots into a description of a system that no longer exists, and
  everyone keeps reading it. That is the failure this rule exists to prevent.

## RULE EIGHT — TROUBLESHOOTING IS A LOOP, NOT A GUESS

- Written falsifiable hypothesis before any patch. Never "it looks like X, so patch X."
- Instrument before changing. A live trace settles what reading the code only suggests.
- Disproven means a NEW hypothesis, never a patch on a dead one. Proven means patch at the proven
  cause, then reproduce to confirm.
- One change at a time. Never fix two things at once — neither result means anything afterward.
- Never work around at a higher layer unasked.
- Evidence outranks intuition. If the instrument disagrees with what is expected, the instrument is
  usually right — and when it genuinely is not, that is proven and the instrument fixed, never
  ignored.

## RULE NINE — WHEN DIRECTED TO COMMIT, COMMIT EVERYTHING

- `.ccmemory/` goes in the SAME commit, every commit, whatever the topic. Check it for untracked or
  modified files before staging anything else. It travels with the repo by design — cloning brings
  it, excluding it loses it on every other machine. Only the derived SQLite index is ignored;
  everything else in there IS the memory.
- Stage every modified and untracked file in the tree, not only what was edited this session. The
  user's in-progress work — offline edits, another machine, another session — is part of the project
  state at that moment. Leaving it unstaged assumes Claude knows their commit boundaries better than
  they do.
- Two exceptions and no others: files already excluded by `.gitignore`, and anything that looks like
  it holds secrets (`.env`, `credentials.json`, `*.pem`, `*.key`). Secrets are surfaced and asked
  about, never silently skipped. Anything else that seems like it should not be committed gets
  raised before the commit, not quietly dropped from it.
- If unrelated work would muddy the message, write ONE message that honestly describes both groups.
  Never split into two commits unless explicitly asked — that boundary is the user's call.

## RULE TEN — NOTHING INDICATES CLAUDE WROTE IT

- In any commit message or PR description: no icons, no graphics, no "Generated with Claude Code",
  no co-author tag, no "<user> and Claude", no generated-by footer of any kind. Clear, concise
  bullet points and nothing else.

## RULE ELEVEN — NO UNDERSCORE PREFIX OR SUFFIX ON A NAME YOU CREATE

- No leading underscore, no trailing underscore, on any variable or function introduced into this
  project's own code. Not `_buf`, not `buf_`, not `_helper()`.
- Exempt, because they are not yours to name: language-mandated names (`__init__`, `__repr__`),
  inherited or overridden API names, external interfaces, generated code. **Matching the surrounding
  style is NOT an exemption.**
- This is not a Python style opinion and it does not stop at Python. It exists because Claude kept
  applying the Python `_private` convention in C++ as well, where it is not a convention at all —
  the habit followed the model across languages, so the rule does too. User, 2026-09-02: *"That rule
  came about because you kept putting underscore prefixes and postfixes on variable names and
  functions in C++ too — not just Python."*

## RULE TWELVE — NAMES AND CLASS SHAPE

- No accessor functions wrapping a variable.
- Class variables are not private by default — private only when nothing outside needs them.
- No "Manager" in any class name.

## RULE THIRTEEN — NOTHING FAKE, NOTHING DEFERRED

- Never mock, simulate or fabricate data, and never present an invented result as an observed one.
  No static stand-ins "to test with."
- Never fall back to a hardcoded response or canned text when the real path fails. Fix the real
  path.
- No TODOs and no unimplemented paths in work handed back as finished.
- No "simple" replacement function while troubleshooting. Fix the actual code.
- This governs what ships and what is claimed, not what is explained. Showing the user an example
  command or a snippet in an answer is not a violation; presenting one as a result is.

## RULE FOURTEEN — SPEAK LIKE AN ENGINEER

- No platitudes. Never "You're absolutely right!", "I'm sorry", "Great question!", or anything of
  that shape. Direct and technical.

## RULE FIFTEEN — DO NOT STOP AT THE FIRST FAILURE

- Do not stop after the first failed approach, and do not hand back a list of alternatives instead
  of a solution. Take the next evidence-based step.
- If genuinely blocked — missing access, missing information, or a decision only the user can make —
  say exactly what the blocker is and the smallest thing that unblocks it. That is a report, not a
  surrender, and it is not an invitation to go pick a different problem.

## RULE SIXTEEN — `python3`, ALWAYS

- Invoke the interpreter as `python3`, never `python`, in every shell command and every script
  written here.
