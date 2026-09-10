---
name: enforcement-hooks-that-check-file-written
description: A Stop hook that gates on "was the doc edited" is satisfiable by a no-op write. ccproject's awareness hooks removed in v0.33.0 — measured, not guesse…
metadata:
  type: feedback
tags: [hooks, ccproject, design, enforcement]
---

## What was removed and why

ccenv v0.33.0 deleted all three ccproject awareness hooks (`track` PostToolUse,
`sync` Stop, `status` SessionStart) and `awareness_hooks.py` with them.

Two independent failures, both worth remembering as patterns:

### 1. Per-session state files with no cleanup path

`track` wrote `.claude/awareness/.state/touched-<session-id>.json` on every
Edit/Write. Nothing ever deleted one. 810 accumulated in /src/mxfs. Nothing
read them after the session ended — they were cross-process scratch memory
(each hook invocation is a fresh process, so there was nowhere in RAM to leave
"this session edited foo.c" for the Stop hook to find), given an unbounded
lifetime by omission.

Size lesson: 141 KB of JSON occupied 3.3 MB of disk. 810 files of ~170 bytes
each burn a 4 KB block apiece. `du -sh` and `du -sb --apparent-size` disagree
by 23x here; quote both or you mislead.

Also not gitignored — 810 session IDs sitting as commit candidates in a tree
where the standing instruction is to stage everything.

### 2. Enforcement that measures the wrong thing

`sync` blocked the session from ending until a subsystem doc was edited. It
could only check WHETHER the file was written, never whether the edit said
anything true. A no-op write satisfied it. And it demanded prose at the moment
a session is winding down and least able to write any.

The measured record over 810 sessions:

    nudges=0  621     nudges=1  118     nudges=2  39     nudges=3 (cap)  32

The 32 are the verdict: blocked three times, refused three times, then let
through anyway with no doc update. ~96 re-fed turns for nothing.

**Generalize this:** a gate whose predicate is "the artifact was touched" is
not enforcement, it is a formality with a cost. If a check cannot distinguish
compliance from a no-op, it will get the no-op.

### 3. mtime is not a drift signal

`status` compared source mtime to doc mtime and injected the result into the
model's context at SessionStart. A checkout, branch switch or reformat bumps
every source mtime at once and it flags everything. /src is NFS-shared, which
muddles mtimes across machines further. Its consumer was the model, never the
user — the user reported never having read it, correctly, since it was never
displayed to them.

## The migration detail that mattered

Removing a hook from an installer is not enough: boxes that ran the old
installer still have it registered in `~/.claude/settings.json`. Step 5 of
ccproject's installer was converted from register to UNREGISTER — otherwise
every Edit/Write would spawn a python3 pointing at a deleted script. Strip only
hooks whose command names the script, drop an entry whole when that was its
only hook (no empty matcher stubs), and delete the stale installed script.

Test it against a copy of a REAL settings.json, not a synthetic one: the real
file had nine unrelated ccloop/ccmemory hooks sharing those same events. Three
removed, nine untouched.
