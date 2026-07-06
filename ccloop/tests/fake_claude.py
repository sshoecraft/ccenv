#!/usr/bin/env python3
"""A stand-in for the ``claude`` binary used by the runner integration tests.

It honors the env vars ccloop sets (CCLOOP_TRANSCRIPT_PATH,
CCLOOP_RESUME_FILE) and is steered by extra test-only env vars:

  FAKE_MODE        work (default) | toolong | wall | sleep | noprogress | launchfail
  FAKE_COUNTER     path to an invocation-counter file
  FAKE_DONE_AFTER  in 'work' mode, write DONE to the resume file once the
                   invocation count reaches this value (converges the loop)
  FAKE_LAUNCHFAIL_TIMES    in 'launchfail' mode, fail this many launches then
                           recover into 'work' (0 = always fail)
  FAKE_LAUNCHFAIL_COUNTER  counter file backing FAKE_LAUNCHFAIL_TIMES
"""

import json
import os
import sys
from pathlib import Path


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def write_transcript(path, assistant_turns=1):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for _ in range(assistant_turns):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "did some work"},
                    {"type": "tool_use", "name": "Write",
                     "input": {"file_path": "/tmp/example.py"}, "id": "t1"},
                ],
                "usage": {
                    "input_tokens": 1200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 15,
                },
            },
        }))
        lines.append(json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "wrote file", "is_error": False},
            ]},
        }))
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main():
    mode = os.environ.get("FAKE_MODE", "work")
    transcript = os.environ.get("CCLOOP_TRANSCRIPT_PATH")
    resume = os.environ.get("CCLOOP_RESUME_FILE")

    if mode == "toolong":
        # Wall hit with NO prior work in this session = the fed prompt itself
        # is too big to start. No transcript written → runner aborts.
        sys.stdout.write("Prompt is too long\n")
        sys.stdout.flush()
        return 1

    if mode == "wall":
        # Wall hit AFTER real work = the context window filled mid-session.
        # A real assistant turn IS written, so the runner must RELAY (summarize
        # + fresh session), not abort.
        if transcript:
            write_transcript(transcript, assistant_turns=1)
        sys.stdout.write("Prompt is too long\n")
        sys.stdout.flush()
        return 1

    if mode == "sleep":
        # Simulate an interactive session that writes a transcript then waits
        # (until the watcher terminates it). Honors SIGTERM by default.
        if transcript:
            write_transcript(transcript, assistant_turns=1)
        import time as _t
        _t.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
        return 0

    if mode == "noprogress":
        emit({"type": "system", "subtype": "init"})
        if transcript:
            write_transcript(transcript, assistant_turns=0)
        emit({"type": "result", "total_cost_usd": 0.0,
              "num_turns": 0, "duration_ms": 5, "subtype": "success"})
        return 0

    if mode == "launchfail":
        # Simulate the model endpoint/gateway being unreachable at startup:
        # exit nonzero WITHOUT writing a transcript, exactly like the child
        # dying before it can open a session. Optionally recover after
        # FAKE_LAUNCHFAIL_TIMES failures (counted in FAKE_LAUNCHFAIL_COUNTER)
        # so a test can exercise retry-then-succeed.
        times = int(os.environ.get("FAKE_LAUNCHFAIL_TIMES", "0"))  # 0 = always fail
        lf_counter = os.environ.get("FAKE_LAUNCHFAIL_COUNTER")
        n = 1
        if lf_counter:
            try:
                n = int(Path(lf_counter).read_text()) + 1
            except (OSError, ValueError):
                n = 1
            Path(lf_counter).write_text(str(n))
        if times <= 0 or n <= times:
            sys.stderr.write(
                "fake-claude: failed to reach model endpoint (connection refused)\n"
            )
            sys.stderr.flush()
            return 1
        # Recovered — fall through to normal 'work' behavior below.
        mode = "work"

    # mode == "work"
    count = 1
    counter = os.environ.get("FAKE_COUNTER")
    if counter:
        try:
            count = int(Path(counter).read_text()) + 1
        except (OSError, ValueError):
            count = 1
        Path(counter).write_text(str(count))

    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": f"working (invocation {count})"},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/tmp/example.py"}, "id": "t1"},
    ]}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": "wrote file", "is_error": False},
    ]}})

    if transcript:
        write_transcript(transcript, assistant_turns=1)

    done_after = os.environ.get("FAKE_DONE_AFTER")
    if done_after and resume and count >= int(done_after):
        Path(resume).write_text("DONE\n", encoding="utf-8")

    emit({"type": "result", "total_cost_usd": 0.01,
          "num_turns": 1, "duration_ms": 1234, "subtype": "success"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
