"""Tests for the statusline script: cache write + output rendering.

Stdlib only. Run with: python3 tests/test_statusline.py
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
STATUSLINE = REPO_ROOT / "statusline.py"


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ccusage-stl-test-")
        self.env = os.environ.copy()
        self.env["TMPDIR"] = self.tmpdir
        # Run statusline.py without TERM tricks; ANSI codes are still emitted
        # but tests strip them when needed.
        self.cache_file = Path(self.tmpdir) / f"ccusage-{os.getuid()}.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, payload):
        """Invoke statusline.py with payload as JSON on stdin. Returns stdout."""
        proc = subprocess.run(
            [sys.executable, str(STATUSLINE)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self.env,
            check=True,
        )
        return proc.stdout

    def test_writes_cache_atomically(self):
        payload = {"context_window": {"context_window_size": 1_000, "used_percentage": 1}}
        self._run(payload)
        self.assertTrue(self.cache_file.exists())
        self.assertEqual(json.loads(self.cache_file.read_text()), payload)

    def test_cache_file_is_mode_0600(self):
        self._run({"x": 1})
        mode = self.cache_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_renders_context_only(self):
        payload = {
            "context_window": {"context_window_size": 1_000_000, "used_percentage": 12.5},
        }
        out = self._run(payload)
        self.assertEqual(out, "125.0k/1.0M tokens (12%)")

    def test_renders_with_rate_limits(self):
        now = int(time.time())
        payload = {
            "context_window": {"context_window_size": 1_000_000, "used_percentage": 12.5},
            "rate_limits": {
                "five_hour": {"used_percentage": 20, "resets_at": now + 3600},
                "seven_day": {"used_percentage": 15, "resets_at": now + 86400},
            },
        }
        out = self._run(payload)
        # Strip ANSI escapes for stable comparison; threshold colors depend on elapsed time.
        clean = out.replace("\033[31m", "").replace("\033[0m", "")
        self.assertEqual(clean, "125.0k/1.0M tokens (12%) | 20%/80% (60m) | 15%/86% (24h)")

    def test_threshold_color_when_over(self):
        now = int(time.time())
        # 5-hour: only 1 hour left = 80% elapsed = threshold 80; pct 90 > 80 → red.
        payload = {
            "context_window": {"context_window_size": 1_000, "used_percentage": 1},
            "rate_limits": {
                "five_hour": {"used_percentage": 90, "resets_at": now + 3600},
            },
        }
        out = self._run(payload)
        self.assertIn("\033[31m90%\033[0m/80%", out)

    def test_no_color_when_under_threshold(self):
        now = int(time.time())
        # 5-hour: only 1 hour left = 80% elapsed = threshold 80; pct 50 < 80 → no red.
        payload = {
            "context_window": {"context_window_size": 1_000, "used_percentage": 1},
            "rate_limits": {
                "five_hour": {"used_percentage": 50, "resets_at": now + 3600},
            },
        }
        out = self._run(payload)
        self.assertNotIn("\033[31m", out)
        self.assertIn("50%/80%", out)

    def test_missing_context_window_silent(self):
        out = self._run({"rate_limits": {}})
        self.assertEqual(out, "")

    def test_invalid_json_no_crash_no_output(self):
        proc = subprocess.run(
            [sys.executable, str(STATUSLINE)],
            input="not valid json",
            capture_output=True,
            text=True,
            env=self.env,
            check=True,
        )
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
