"""Live probe, run by hand (not collected by pytest): what decides when Claude Code
gives up on a silent call to a worker's ccdirector MCP server.

It launches a headless claude with the worker's MCP server, the way ccdirector
launches workers, but with the server's stdin teed to a file, and asks it to call
`run` on `sleep SECONDS`. Afterwards it prints:
- whether claude's `tools/call` carried `_meta.progressToken` (without one the
  server's heartbeat sends nothing, and the call is silent while it waits);
- whether claude sent `notifications/cancelled` when it gave up;
- claude's reply and how long the call took.

    python3 tests/probe_mcp_timeout.py --seconds 90 --timeout-ms 60000   # expect an abort near 60 s
    python3 tests/probe_mcp_timeout.py --seconds 90 --timeout-ms 300000  # expect the run to finish
    python3 tests/probe_mcp_timeout.py --seconds 90                      # no per-server timeout

Costs one or two small requests on the given model.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "src"))

from ccdirector import plan, work, workers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--timeout-ms", type=int, default=0,
                        help="per-server timeout to put in the MCP config (0: none)")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ccdirector-timeout-"))
    current = {"goal": "g", "source": "directive", "gate": "", "criteria": ["c"],
               "items": [{"id": "a", "what": "a", "serves": ["c"], "priority": 1,
                          "paths": [], "check": "true", "size": "S"}], "risks": []}
    plan.write(root, current)
    work.sync(root, current)
    config = workers.mcp_config(root, "worker-1")
    server = config["mcpServers"]["ccdirector"]
    server["env"]["PYTHONPATH"] = os.pathsep.join([str(HERE / "src"), str(HERE.parent / "ccenvmcp")])
    captured = root / "requests.jsonl"
    inner = " ".join(shlex.quote(part) for part in [server["command"], *server["args"]])
    server["command"], server["args"] = "bash", ["-c", f"tee {shlex.quote(str(captured))} | {inner}"]
    if args.timeout_ms:
        server["timeout"] = args.timeout_ms
    path = workers.mcp_config_path(root, "worker-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    prompt = (f"Call the ccdirector MCP tool `run` with command \"sleep {args.seconds}; echo "
              "PROBE-OK\". When it returns, reply with its exit code and the last line of its "
              "tail. If it fails, reply with the error text, nothing else.")
    started = time.time()
    done = subprocess.run(["claude", "-p", "--model", args.model, "--mcp-config", str(path),
                           "--permission-mode", "bypassPermissions", prompt],
                          capture_output=True, text=True, cwd=str(root),
                          timeout=args.seconds + 2400)
    took = time.time() - started
    requests = []
    for line in captured.read_text().splitlines() if captured.exists() else []:
        try:
            requests.append(json.loads(line))
        except ValueError:
            pass
    calls = [r for r in requests if r.get("method") == "tools/call"]
    tokens = [((r.get("params") or {}).get("_meta") or {}).get("progressToken") for r in calls]
    cancels = [r for r in requests if r.get("method") == "notifications/cancelled"]
    print(f"per-server timeout: {args.timeout_ms or 'none'} ms; command sleeps {args.seconds}s")
    print(f"claude exit {done.returncode} after {took:.0f}s")
    print(f"tools/call requests: {len(calls)}; progressToken on each: {tokens}")
    print(f"notifications/cancelled sent: {len(cancels)}")
    print("reply:", done.stdout.strip()[-600:])
    if done.stderr.strip():
        print("stderr:", done.stderr.strip()[-400:])
    print(f"kept: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
