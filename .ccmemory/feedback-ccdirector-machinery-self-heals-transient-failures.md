---
name: feedback-ccdirector-machinery-self-heals-transient-failures
description: ccdirector's own machinery (worker launches, retries) must never latch off on a failure count; API/network outages are expected and must self-recover.
metadata:
  type: feedback
---

The no-give-up rule covers ccdirector's internals too, not only the director's judgement. The worker supervisor had `halted = True` after 3 quick worker deaths, never cleared. A few minutes of Cloudflare 403s left an mxfs run with zero workers for hours, and only a manual `ccdirector start` restart helped.

**Why:** The user: "Anthropic is going to be unavailable at times ... the director is just going to fall flat on its face and never recover and That sucks." They asked for a real worker pool with restart of dead workers, not just a latch fix.

**How to apply:** Any failure path in ccdirector or ccloop that stops trying after N failures is a defect unless the user asked for that bound. Back off (doubling, capped) and keep probing, and resume on the first success. Since ccdirector 0.15.0 this is `workers.Pool`: per-slot backoff, outage mode with one try at a time, stall kill, tree kill. Related trap: ccloop starts claude with `start_new_session=True`, so `killpg(worker_pid)` misses claude and its tools. Walk `/proc` ppids instead. Related: [[feedback-ccdirector-never-gives-up-by-default]].
