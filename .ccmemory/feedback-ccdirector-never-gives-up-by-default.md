---
name: feedback-ccdirector-never-gives-up-by-default
description: ccdirector keeps replanning on hard problems with no default limit, but stops and says exactly what is missing when truly blocked.
metadata:
  type: feedback
---

A ccdirector run must keep trying on a hard problem: no default retry limit, round limit or count-based give-up. A bound exists only when the user explicitly asks for one on that run. But when it is truly blocked, it must stop and say so clearly: a needed resource is unavailable (machine, gateway, credential, data) or no amount of replanning can fix it.

**Why:** The user rejected a default 5-round limit: "If I wanted something to quit trying after a certain amount of times, I wouldn't ask you to do it." Then, on being blocked: "If there's a resource that it needs, like the interactive broker gateway ... that is not available, then the director needs to stop and say that. Clearly. You don't just keep going."

**How to apply:** The stop is decided by what is blocking, never by how many attempts were made. When a run is not converging, change what it tries and make that visible. Related: [[feedback-ccdirector-never-asks-reuses-plan]].
