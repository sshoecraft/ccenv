---
name: feedback-ccdirector-never-gives-up-by-default
description: ccdirector keeps going until the gate passes; it stops early only when a dependency is down, proven by a failing probe — never on judgement.
metadata:
  type: feedback
---

A ccdirector run must keep trying on a hard problem: no default retry limit, round limit or count-based give-up. A bound exists only when the user explicitly asks for one on that run. The one early stop: a dependency the work already uses is down (machine, gateway, credential, a data feed that worked) — and that must be measured, not judged.

**Why:** The user rejected a default 5-round limit: "If I wanted something to quit trying after a certain amount of times, I wouldn't ask you to do it." On being blocked: "If there's a resource that it needs, like the interactive broker gateway ... that is not available, then the director needs to stop and say that." Then (2026-11-11) a warrior director called goal-stuck with 7 of 9 criteria red because Astra saw "no lever left", it wanted a paper smoke run approved, it wanted a bar changed, and it wanted Level 2 data the project never had. The user: "that dumbass exited vs continuing forever until complete". The prompt had allowed "no replan can fix it" and "a decision only the user can make" as stop reasons, and `goal-stuck` took any reason on trust.

**How to apply:** Since ccdirector 0.14.0, `goal-stuck` requires `--probe CMD` (succeeds when the dependency works); ccdirector runs it and refuses while it succeeds, and refuses trivially failing probes. Never reintroduce a judgement-based exit: out of ideas, advisor says stop, a call that seems to be the user's, data never had — the director decides, records in `risks`, plans the work, or tries a different kind of change. Related: [[feedback-ccdirector-never-asks-reuses-plan]].
