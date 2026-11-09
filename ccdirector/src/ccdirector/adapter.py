"""One `claude -p` call that must come back as a schema-validated JSON object.

Ported from warrior/adapters/claude.py (itself from builder and llm-bench), with
the differences an agent that reads a project needs:

- It runs in the project root, not the system temp directory. warrior's grader
  needs no files, so it runs where the CLI cannot list anything; the
  goal-finder's whole job is to read the project.
- It gets a fixed set of read-only built-in tools (`--tools Read Grep Glob`)
  instead of none. `--tools` limits which tools exist at all, so nothing the
  user's settings allow (Bash rules, say) can be reached.
- The system prompt comes from a file (`--system-prompt-file`), as the
  aitrader auditor does.

Kept from warrior: `--output-format json --json-schema`, the answer read from
`structured_output`; a usage-limit notice raised as status 429 and never
returned as an answer; CLAUDECODE removed from the child's environment (a
nested CLI refuses to start under one); the prompt on stdin, which keeps it
out of `ps` and clear of the argv size limit; no `--bare`, which would accept
only ANTHROPIC_API_KEY auth and skip the logged-in account.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Phrases the CLI uses when a usage or session limit stops it. It reports them
#: as the turn's text, not as an error code. Taken from warrior, which took them
#: from builder, where they were collected from real limit notices.
RATE_LIMIT_MARKERS = (
    "usage limit reached",
    "session limit",
    "claude ai usage limit",
    "limit reached · resets",
    "limit reached, resets",
    "limit · resets",
    "approaching the usage limit",
    "hit your session limit",
    "5-hour limit reached",
)


class AdapterError(RuntimeError):
    """The CLI could not be run, or did not give a usable answer.

    `status` is the API's status code when the CLI reported one (429 for a usage
    limit, 404 for an unknown model), and None for a timeout, a process that
    could not start, or an answer that failed validation.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Reply:
    """One answer and what it cost. `data` is the validated structured output."""

    data: dict = field(default_factory=dict)
    text: str = ""
    model: str = ""
    cost_usd: float = 0.0
    elapsed: float = 0.0
    turns: int = 0


def looks_rate_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def interpret(returncode: int, stdout: str, stderr: str) -> Reply:
    """Turn one finished `claude -p --output-format json --json-schema` run into
    a Reply, or raise AdapterError. Pure: everything it needs is its arguments,
    so it is tested against recorded real runs."""
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    try:
        result = json.loads(out.splitlines()[-1]) if out else {}
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    text = str(result.get("result") or "")
    status = result.get("api_error_status")
    if status == 429 or looks_rate_limited(" ".join([text, err])):
        raise AdapterError(f"claude -p hit a usage limit: {(text or err)[:300]}", status=429)
    if returncode != 0 or result.get("is_error") or result.get("type") != "result":
        raise AdapterError(
            f"claude -p exited {returncode} ({result.get('terminal_reason') or 'no result'}): "
            f"{(text or err or out)[:300]}",
            status=status if isinstance(status, int) else None)
    data = result.get("structured_output")
    if not isinstance(data, dict):
        raise AdapterError(f"claude -p returned no structured_output: {text[:300]!r}")
    models = list((result.get("modelUsage") or {}).keys())
    return Reply(data=data, text=text, model=models[0] if models else "",
                 cost_usd=float(result.get("total_cost_usd") or 0.0),
                 elapsed=float(result.get("duration_ms") or 0) / 1000.0,
                 turns=int(result.get("num_turns") or 0))


def command(binary: str, system_file: Path, schema: dict, tools: list[str],
            model: str | None) -> list[str]:
    cmd = [binary, "-p", "--output-format", "json",
           "--json-schema", json.dumps(schema),
           "--system-prompt-file", str(system_file),
           "--strict-mcp-config", "--no-session-persistence",
           "--tools", ",".join(tools) if tools else ""]
    if model:
        cmd += ["--model", model]
    return cmd


def child_environment() -> dict:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    return env


def request(system_file: Path, prompt: str, schema: dict, cwd: Path,
            tools: list[str], timeout: float, model: str | None = None,
            binary: str | None = None) -> Reply:
    """Run one `claude -p` in `cwd` and return its validated structured answer."""
    exe = binary or shutil.which("claude") or "claude"
    started = time.monotonic()
    try:
        done = subprocess.run(command(exe, system_file, schema, tools, model),
                              input=prompt.encode(), capture_output=True,
                              timeout=timeout, env=child_environment(), cwd=str(cwd))
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"claude -p gave no answer within {timeout:.0f}s") from exc
    except OSError as exc:
        raise AdapterError(f"claude -p could not start: {exc}") from exc
    reply = interpret(done.returncode, done.stdout.decode(errors="replace"),
                      done.stderr.decode(errors="replace"))
    if not reply.elapsed:
        reply.elapsed = time.monotonic() - started
    return reply
