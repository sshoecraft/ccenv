"""Integration tests for the ccusage MCP server.

Spawns run_server.py as a subprocess and exercises the stdio/JSON-RPC interface
the same way an MCP client (Claude Code, Claude Desktop) would. Stdlib only —
no pytest. Run with: python3 tests/test_server.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER = REPO_ROOT / "run_server.py"


class MCPClient:
    """Minimal JSON-RPC stdio client for the ccusage MCP server."""

    def __init__(self, env=None):
        self.proc = subprocess.Popen(
            [sys.executable, str(RUN_SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self.next_id = 1

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _recv(self):
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()
            raise RuntimeError(f"server closed stdout. stderr={err}")
        return json.loads(line)

    def initialize(self):
        self._send({
            "jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ccusage-tests", "version": "1.0"},
            },
        })
        self.next_id += 1
        resp = self._recv()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def list_tools(self):
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": "tools/list"})
        self.next_id += 1
        return self._recv()

    def call_tool(self, name, arguments=None):
        self._send({
            "jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        self.next_id += 1
        return self._recv()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        finally:
            for stream in (self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    stream.close()


class CCUsageServerTests(unittest.TestCase):
    """Each test runs against a fresh tmpdir-based cache so they are hermetic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ccusage-test-")
        self.env = os.environ.copy()
        self.env["TMPDIR"] = self.tmpdir
        self.cache_file = Path(self.tmpdir) / f"ccusage-{os.getuid()}.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_cache(self, payload):
        self.cache_file.write_text(json.dumps(payload))

    def _client(self):
        return MCPClient(env=self.env)

    # --- tests ---

    def test_initialize_advertises_server_name(self):
        c = self._client()
        try:
            resp = c.initialize()
            self.assertEqual(resp["result"]["serverInfo"]["name"], "ccusage")
        finally:
            c.close()

    def test_lists_both_tools(self):
        c = self._client()
        try:
            c.initialize()
            resp = c.list_tools()
            names = {t["name"] for t in resp["result"]["tools"]}
            self.assertEqual(names, {"get_context_usage", "get_context_usage_raw"})
        finally:
            c.close()

    def test_get_context_usage_formats_known_payload(self):
        now = int(time.time())
        self._write_cache({
            "context_window": {
                "context_window_size": 1_000_000,
                "used_percentage": 25.0,
            },
            "rate_limits": {
                "five_hour": {"used_percentage": 10.0, "resets_at": now + 3600},
                "seven_day": {"used_percentage": 50.0, "resets_at": now + 86400},
            },
        })
        c = self._client()
        try:
            c.initialize()
            resp = c.call_tool("get_context_usage")
            text = resp["result"]["structuredContent"]["result"]
            self.assertIn("250.0k/1.0M tokens", text)
            self.assertIn("25.0% used", text)
            self.assertIn("750.0k remaining", text)
            self.assertIn("5-hour limit: 10.0% used", text)
            self.assertIn("7-day limit: 50.0% used", text)
        finally:
            c.close()

    def test_get_context_usage_raw_returns_structured_data(self):
        payload = {
            "context_window": {"context_window_size": 200_000, "used_percentage": 5.0},
            "rate_limits": {},
        }
        self._write_cache(payload)
        c = self._client()
        try:
            c.initialize()
            resp = c.call_tool("get_context_usage_raw")
            text = resp["result"]["content"][0]["text"]
            parsed = json.loads(text)
            self.assertEqual(parsed["data"], payload)
            self.assertGreaterEqual(parsed["cache_age_seconds"], 0)
        finally:
            c.close()

    def test_missing_cache_returns_isError(self):
        # No cache file written.
        c = self._client()
        try:
            c.initialize()
            resp = c.call_tool("get_context_usage")
            self.assertTrue(resp["result"]["isError"])
            err_text = resp["result"]["content"][0]["text"]
            self.assertIn("No cache file", err_text)
        finally:
            c.close()

    def test_partial_payload_omits_missing_sections(self):
        # Only context_window — no rate_limits.
        self._write_cache({
            "context_window": {"context_window_size": 500_000, "used_percentage": 80.0},
        })
        c = self._client()
        try:
            c.initialize()
            resp = c.call_tool("get_context_usage")
            text = resp["result"]["structuredContent"]["result"]
            self.assertIn("400.0k/500.0k tokens", text)
            self.assertNotIn("5-hour limit", text)
            self.assertNotIn("7-day limit", text)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
