"""ccprospect — prospective memory for Claude Code projects.

The FUTURE store, sibling of ccmemory (the TIMELESS store). ccmemory answers
"what did we learn" by pull (relevance search); ccprospect answers "what did a
prior session intend to do when X happens" by push (an inbox of fired/due
items evaluated at wake boundaries).

Core objects:
  - contract   — an IMMUTABLE .md file: intention + typed predicate + expiry
                 (+ optional falsifiable ``expect`` and probability bucket)
  - events     — append-only ``events.jsonl``: created/fired/ack/superseded/
                 expired; ALL current state is derived by folding this log
  - inbox      — fired-unacknowledged + due reviews + expiring-soon, computed
                 by evaluating every open predicate at wake time (no daemon)

The load-bearing invariant, mechanical not prompted: a session may stop
paying attention to a prospect (cancel, supersede), but may never rewrite or
escape its outcome — cancelled/superseded contracts keep resolving
counterfactually until their original expiry.
"""

__version__ = "0.1.0"
