---
name: feedback-ccdirector-never-asks-reuses-plan
description: ccdirector must never prompt the user or wait on a decision, and reuses the plan on file unless explicitly told to replan.
metadata:
  type: feedback
---

ccdirector never asks the user a question: no `[Y/n]` approval steps, no hold-for-an-answer commands, no director waiting on a decision. The director makes the call and records it in the plan's `risks`. A plan once written is reused on every later `ccdirector start`, with or without `--goal`, until the user explicitly asks for a new one.

**Why:** The user rejected a proposed "Start workers? [Y/n]" prompt and a `plan-hold` command: "it's asking me a question which as you know it should never ever ever do", and "it should use the plan that was previously created unless I tell it to create a new plan".

**How to apply:** When a ccdirector design seems to need user input, resolve it in the director and write it to `risks`. Do not add a confirmation to fix a sequencing problem. A changed goal does not imply a replan; only `--replan` does.
