---
name: compiled-mcp-tool-availability
description: ccmemory MCP tools going missing: install.sh healing stale registrations, alwaysLoad's real behavior, and defensive protocol design
metadata:
  type: project
tags: [compiled, mcp, ccmemory, install, tool-surface]
---

## Topic

Three incidents, same root class of failure: a Claude Code session cannot see
or use ccmemory's (or another component's) MCP tools, for reasons that span
install-time registration, harness loading semantics, and injected-protocol
text that assumes a tool is there. Chronological.

## 1. install.sh must heal stale registrations, not skip-if-present

Two components independently hit the same bug shape: a binary's registered
path goes stale (user moves from a manual venv to `~/.local/bin`, or from a
pre-PYTHONUSERBASE layout), the registration entry still "exists" by name, and
a skip-if-registered installer walks past it forever. The user was stuck
manually running `claude mcp remove` / re-registering by hand.

- `ccloop`'s own hooks (`PostToolUse → ccloop guard`, `Stop → ccloop
  keepgoing`) are registered on first task run and pinned to the binary path
  at that time. Fix: top-level `install.sh` runs `ccloop install` after every
  `pip3 install --user ccloop`; `ensure_registered()` uses a deliberately
  LOOSE matcher (`_is_ours`: basename is or contains `ccloop`) to catch and
  rewrite any stale entry, including legacy venv paths or a bash-era hook. A
  strict equality matcher would defeat the point — never tighten it.
  [[ccloop-install-heals-own-hooks]]
- MCP server registration has the identical shape: `install.sh`'s
  `register_mcp()` doesn't just check existence — it parses `claude mcp get`'s
  `Command:`/`Args:` output, compares to the command it would register now,
  and if they differ, removes and re-adds. Same failure mode this prevents:
  ccmemory/ccteam pinned to a bare `ccmemory` binary name from a
  pre-PYTHONUSERBASE install, or a macOS path after switching install bases —
  `claude mcp list` shows Failed to connect, and a plain re-run of install.sh
  does nothing because the entry "exists." Any new component's installer must
  go through `register_mcp()` (or replicate compare-and-heal, as
  `ccusage/install.py:register_mcp_user()` does in Python) — never inline a
  bare `claude mcp add`. [[mcp-heal-stale-command-pattern]]

Generalized lesson: a registration check that only asks "is it present" is not
a health check. It must ask "is it present AND CURRENT," or staleness is
permanent and self-inflicted.

## 2. `alwaysLoad` is a bounded deadline, not a load guarantee — and it can erase the tool surface

`install.sh` set `alwaysLoad: true` on ccmemory from v0.6.0. Decompiled from
the CLI binary (2.1.219): `alwaysLoad` servers form a "regular-required" tier
that loads with a **shared, per-tier deadline** (`MCP_CONNECT_TIMEOUT_MS`,
default 5000ms) that is always blocking regardless of
`MCP_CONNECTION_NONBLOCKING`. On expiry the session **starts anyway** — so the
flag never guaranteed the tools were registered, which was its only reason to
exist.

Worse, measured live on a box running Claude Code against
`google/gemma-4-26B-A4B-it` through an OpenAI-compatible proxy: with
`alwaysLoad: true`, ccmemory had **0 successful `memory_list` calls across 122
transcripts** (`No such tool available`), while sibling servers with the flag
unset (broker, journal, scheduler, searxng, ccusage) each logged hundreds of
successful calls on the identical bundle/box/proxy. The server itself was
healthy (`claude mcp list` showed Connected, handshake 0.18s, SessionStart
fired normally) — `alwaysLoad` on a non-Anthropic model made the tools
disappear from the surface entirely rather than degrade gracefully. Fixed by
removing the flag in ccenv v0.13.2 (`strip_always_load()` now deletes the
field instead of setting it).

A related finding was recorded a day earlier (2026-07-24: "it's a deadline,
not a barrier") and NOT acted on before the tool-erasure symptom went live in
production and had to be rediagnosed from scratch. Lesson restated: a note
that says "this mechanism does not do what its name implies" is a finding to
act on immediately, not a footnote to revisit later.

Standing facts about MCP loading, still true: no hook can gate on MCP status
(SessionStart hooks complete before the init event that reports
`mcp_servers`); both loading tiers launch in one concurrent `Promise.all`, so
no load-order trick ("sentinel server that loads last") is implementable; a
model CAN enumerate its own real tool surface by reading its tool list (eager
tools appear as callable functions, deferred ones are named in a
system-reminder, split `mcp__<server>__<tool>` on `__`) — this is in-session
truth, whereas `claude mcp list` spawns a second, separate copy of every
server and can report Connected on a box where the tools were never actually
available to the running session. Enumeration also cannot detect its own
incompleteness (an unregistered server looks identical to "not configured");
any check needs a hardcoded expected set to diff against.
[[mcp-alwaysload-blocks-startup]]

## 3. Injected protocol text must not assume its own remedy tool exists

ccmemory v0.13.0 added session-start protocol text instructing a model that
can't see `memory_list` to call `ToolSearch("select:mcp__ccmemory__memory_list")`
to try to pull in a late-connecting server — the direct successor problem to
item 2, now attacked at the protocol layer instead of the installer layer.
`ToolSearch` itself only exists when the harness has tool-search enabled: on a
session without it, a model that correctly detected the missing tool dead-ended
hunting for the tool with which to find a tool. Observed on a gemma-4-26B
session: it searched its own tool list for `ToolSearch`, didn't find it,
re-read the list, then burned a turn pasting its own reasoning into a `Bash`
call as comments.

This was a narrower regression, not a wash: v0.13.0's actual target (silent
failure — a session with no ccmemory reads exactly like a project with no
memory, so the model confidently concludes "no prior memory exists") did
close; "silently wrong" became "visibly stuck," which is better but not
right. Fixed in v0.13.1 with four rules for any injected "call tool X":

1. Gate it — "IF, and only if, X is in your tool list."
2. State plainly that X's absence is NORMAL and say to SKIP rather than hunt
   for it; a model told to call a missing tool will search for it.
3. Forbid the shell fallback by name — nothing run in bash can register an
   MCP tool, but models will try.
4. Make the terminal/stop state reachable from EVERY branch, not just the one
   that ran X. v0.13.0's "if still unavailable after that, STOP" hung off the
   `ToolSearch` branch only, leaving the no-`ToolSearch` path with no exit.

Surfaced on gemma-4-26B, not Opus — protocol text is executed by whatever
model the user points at the box, including small models driving
`instenv.prompt` fan-outs. Write injected text for the least capable model
that will ever read it. [[prescribed-remedy-must-not-assume-its-own-tool-exists]]

## Cross-cutting takeaways

- Registration and loading are two different failure layers (installer vs.
  harness vs. injected protocol) and each one independently produced a
  "tools silently unavailable" incident. Fixing one layer does not cover the
  others.
- Anything that checks "is the MCP tool there" — a health check, an installer,
  or protocol text read by the model itself — must treat absence as an
  expected, handled case with a defined exit, never an assumed-present
  precondition.
- Weak/non-Anthropic models are the real test case for all of this: they are
  the ones actually driving unattended fan-outs, and they are where every one
  of these three failures was first measured, not where they were first
  suspected.
