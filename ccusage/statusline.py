"""Claude Code statusline that also writes a cache file the ccusage MCP server reads.

Reads the JSON Claude Code pipes on stdin, atomically writes it to the shared
cache path, then prints a one-line status: tokens used/total (with %), 5-hour
rate-limit usage with reset countdown, 7-day rate-limit usage with reset
countdown. Usage that's over the time-elapsed threshold is colored red.
"""

import json
import math
import os
import sys
import time

from paths import cache_path


RED = "\033[31m"
RST = "\033[0m"


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def write_cache(raw: str) -> None:
    """Atomic per-UID cache write. Best-effort: caching errors are silent so
    the statusline still renders even if /tmp is unwritable."""
    cp = cache_path()
    tmp = cp.with_name(f".{cp.name}.{os.getpid()}")
    try:
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
    out = f"{fmt_num(used)}/{fmt_num(total)} tokens ({round(pct)}%)"

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
    write_cache(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    line = render(data)
    if line:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
