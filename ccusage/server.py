"""ccusage MCP server.

Exposes tools that return the current Claude Code context-window and
rate-limit usage. Data source is the JSON cache file written by the
ccusage statusline (statusline.py) after every turn.
"""

import json
import time

from mcp.server.fastmcp import FastMCP

from paths import cache_path

VERSION = "0.1.1"

mcp = FastMCP("ccusage")


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_duration(seconds):
    if seconds <= 0:
        return "0s"
    if seconds < 3600:
        return f"{(seconds + 59) // 60}m"
    if seconds < 86400:
        return f"{(seconds + 3599) // 3600}h"
    return f"{(seconds + 86399) // 86400}d"


def load_cache():
    cp = cache_path()
    if not cp.exists():
        raise FileNotFoundError(
            f"No cache file at {cp}. The statusline must run at least "
            "once per session to populate it."
        )
    age = time.time() - cp.stat().st_mtime
    data = json.loads(cp.read_text())
    return data, age


@mcp.tool()
def get_context_usage() -> str:
    """Return current context-window and rate-limit usage for this Claude Code session.

    Reports actual numbers from Claude Code itself (not estimates):
    - context window: tokens used, total, and % used
    - 5-hour rate limit: % used + time to reset
    - 7-day rate limit: % used + time to reset

    WHEN TO USE THIS:
    - MANDATORY before suggesting the user end the session, hand off, start a
      new session, or wrap up "due to context." Do not guess. Internal
      estimates of context usage are unreliable — call this tool first and
      base the decision on the actual percentage. If context is below ~70%,
      do not suggest stopping.
    - When the user asks about tokens, context, capacity, or session limits.
    - Before agreeing to a long multi-step task, to size the plan against
      remaining budget.

    DO NOT call this every turn — that wastes tokens. Call it when the answer
    would change a decision: stop vs. continue, plan A vs. plan B.
    """
    data, age = load_cache()

    lines = [f"(cache age: {fmt_duration(int(age))})"]

    cw = data.get("context_window") or {}
    total = cw.get("context_window_size")
    pct = cw.get("used_percentage")
    if total is not None and pct is not None:
        used = int(pct * total / 100)
        remaining = total - used
        lines.append(
            f"Context: {fmt_tokens(used)}/{fmt_tokens(total)} tokens "
            f"({pct:.1f}% used, {fmt_tokens(remaining)} remaining)"
        )
    else:
        lines.append("Context: unavailable")

    now = time.time()
    rl = data.get("rate_limits") or {}

    five = rl.get("five_hour") or {}
    if "used_percentage" in five:
        msg = f"5-hour limit: {five['used_percentage']:.1f}% used"
        if "resets_at" in five:
            secs = max(0, int(five["resets_at"] - now))
            msg += f", resets in {fmt_duration(secs)}"
        lines.append(msg)

    week = rl.get("seven_day") or {}
    if "used_percentage" in week:
        msg = f"7-day limit: {week['used_percentage']:.1f}% used"
        if "resets_at" in week:
            secs = max(0, int(week["resets_at"] - now))
            msg += f", resets in {fmt_duration(secs)}"
        lines.append(msg)

    return "\n".join(lines)


@mcp.tool()
def get_context_usage_raw() -> dict:
    """Return the raw JSON Claude Code passed to the statusline, plus cache age in seconds.

    Prefer get_context_usage for a human-readable summary; use this when you
    need exact numbers or fields the summary omits.
    """
    data, age = load_cache()
    return {"cache_age_seconds": int(age), "data": data}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
