"""Claude Code statusline that also writes a cache file the ccusage MCP server reads.

Reads the JSON Claude Code pipes on stdin, atomically writes it to the shared
cache path, then prints a one-line status: tokens used/total (with %), 5-hour
rate-limit usage with reset countdown, 7-day rate-limit usage with reset
countdown. Usage that's over the time-elapsed threshold is colored red.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

from paths import cache_path, state_dir


RED = "\033[31m"
RST = "\033[0m"

# Per-session cache files accumulate (one per session ever run). Prune any
# not written within this window so the state dir stays bounded.
RETENTION_SECONDS = 2 * 86400


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_window(n: int) -> str:
    """Render a context-window size in whichever unit reads cleanly.

    Local-model windows are powers-of-two multiples (262144 = 256*1024), so
    those are shown in binary units -> '256k' (not the decimal '262.1k'), with
    a trailing '.0' stripped. Windows that aren't 1024-aligned (the Anthropic
    200000 / 1000000 windows) stay decimal so they read '200.0k' / '1.0M'
    instead of an ugly binary '195.3k'."""
    if n % 1024 == 0:
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.1f}M"
        s = f"{n / 1024:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return f"{s}k"
    return fmt_num(n)


def write_cache(raw: str, session_id: str | None = None) -> None:
    """Atomic per-session cache write. Best-effort: caching errors are silent
    so the statusline still renders even if the state dir is unwritable."""
    cp = cache_path(session_id)
    tmp = cp.with_name(f".{cp.name}.{os.getpid()}")
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        old_umask = os.umask(0o077)
        try:
            tmp.write_text(raw)
        finally:
            os.umask(old_umask)
        os.replace(tmp, cp)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def prune_stale(now: float | None = None) -> None:
    """Best-effort removal of per-session cache files older than the
    retention window, so the state dir doesn't grow one file per session
    forever."""
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    try:
        files = list(state_dir().glob("*.json"))
    except OSError:
        return
    for p in files:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def fmt_usage(actual: float, threshold: float) -> str:
    """Percent / threshold, with the actual % in red when it exceeds threshold."""
    a = round(actual)
    t = round(threshold)
    if actual > threshold:
        return f"{RED}{a}%{RST}/{t}%"
    return f"{a}%/{t}%"


def render_rate_limit(window: dict, window_secs: int, reset_unit: str) -> str | None:
    pct = window.get("used_percentage")
    if pct is None:
        return None
    resets_at = window.get("resets_at")
    if resets_at is None:
        return f" | {round(pct)}%"
    secs_left = max(0, min(window_secs, int(resets_at - time.time())))
    elapsed = window_secs - secs_left
    threshold = elapsed * 100 / window_secs
    if reset_unit == "m":
        unit_left = math.ceil(secs_left / 60)
    elif reset_unit == "h":
        unit_left = math.ceil(secs_left / 3600)
    else:
        unit_left = secs_left
    return f" | {fmt_usage(pct, threshold)} ({unit_left}{reset_unit})"


def render(data: dict) -> str:
    cw = data.get("context_window") or {}
    total = cw.get("context_window_size")
    pct = cw.get("used_percentage")
    if total is None or pct is None:
        return ""

    used = int(pct * total / 100)
    out = f"{fmt_num(used)}/{fmt_window(total)} tokens ({round(pct)}%)"

    rl = data.get("rate_limits") or {}
    five = render_rate_limit(rl.get("five_hour") or {}, 18_000, "m")
    if five:
        out += five
    week = render_rate_limit(rl.get("seven_day") or {}, 604_800, "h")
    if week:
        out += week

    return out


def main() -> int:
    raw = sys.stdin.read()
    # Parse first so the cache can be keyed by this session's id. If the
    # payload isn't JSON we still cache it (under the per-UID fallback name)
    # so a reader has something, then bail before rendering.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        write_cache(raw)
        return 0
    write_cache(raw, data.get("session_id"))
    prune_stale()
    line = render(data)
    if line:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
