---
name: prescribed-remedy-must-not-assume-its-own-tool-exists
description: Injected protocol text that prescribes a tool (ToolSearch) must gate on that tool existing — ccmemory v0.13.0 turned a silent failure into a dead-end…
metadata:
  type: project
---

# A prescribed remedy must not assume its own tool exists

## The bug (ccmemory v0.13.0 -> v0.13.1)

`SESSION_PROTOCOL` is injected into every session by the ccmemory
SessionStart hook. v0.13.0 added handling for "`memory_list` is not in your
tool list at all," phrased as a bare imperative:

> Call `ToolSearch("select:mcp__ccmemory__memory_list")` once to try to pull
> in a late-connecting server.

`ToolSearch` only exists when the harness has tool-search enabled. On a
session without it, a model that CORRECTLY detected the missing `memory_list`
then dead-ended looking for the tool with which to find a tool.

Observed on a gemma-4-26B session: it searched its tool list for `ToolSearch`,
did not find it, re-read the list, then burned a turn on a `Bash` call whose
command body was its own reasoning pasted in as `#` comments.

## Why it still counted as progress

v0.13.0's actual target was SILENT failure — a session with no ccmemory reads
exactly like a project that has no memory, so the model proceeds confidently
on false "there is no prior memory on this" conclusions. That hole DID close:
the model noticed and said so.

The regression was narrower: "silently wrong" became "visibly stuck." Better,
not right. Do not let the second failure mode discredit the first fix, and do
not let the first fix excuse the second.

## The rule

Any instruction injected into a model's context that says "call tool X":

1. Gate it — "IF, and only if, X is in your tool list."
2. State that X's absence is NORMAL, and say explicitly to SKIP rather than
   hunt for it. A model told to call a missing tool will look for it.
3. Forbid the shell fallback by name. There is no shell path to an MCP tool;
   nothing run in bash can register one. Models WILL try this otherwise.
4. Make the terminal state reachable from EVERY branch, not just the branch
   that ran X. v0.13.0's "If the tool is still unavailable after that, STOP"
   hung off the ToolSearch branch, so the no-ToolSearch path had no exit.

## Weak models are the test case

This surfaced on gemma-4-26B, not on Opus. Protocol text is executed by
whatever model the user points at the box — including the small ones driving
`instenv.prompt` fan-outs. Write the injected text for the least capable model
that will ever read it.
