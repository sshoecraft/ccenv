---
name: feedback-ccdirector-workers-own-a-unit-until-solved
description: ccdirector's purpose: turn any goal into a plan, split it into independent tasks, and have each worker own one task start to finish until done; never…
metadata:
  type: feedback
---

The user's design intent for ccdirector, stated with force after it drifted:
started in any directory with any goal, the director writes a plan, splits it
into tasks that can be worked in parallel, and hands one task to each worker.
The worker owns that task from start to finish and works on it **until it is
done**: build, test, wait for results, fix, repeat, in one session with its
context. Defects are only one kind of task (when the goal is a release gate);
the design is not about defects.

What the user does NOT want: tasks that are single steps, a worker doing one
step and parking it back to the director, auto-park after N failed checks, the
director revising work mid-task, other workers picking up a half-done task
cold. Quote: "not do one work item and then return to the director. Why would
I want that? I wanted to work on this until it's solved."

The benchmark the user holds it to: two plain Claude sessions, each told "work
on X" for two unrelated pieces of work, finish about 2x as fast as one. N
workers must do at least that. Extra token cost for the speed is accepted;
coordination overhead that kills the speed is not.

When changing ccdirector, judge every hand-off between worker and director
against this: a hand-off mid-task is a defect in the design, not a feature.
