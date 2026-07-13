"""Time helpers shared by every module.

All timestamps in contracts and events are UTC ISO-8601 with a trailing
``Z``. Python 3.9's ``datetime.fromisoformat`` cannot parse ``Z`` (that
landed in 3.11), so parsing goes through :func:`parse_iso` everywhere —
never call ``fromisoformat`` directly on stored values.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_now() -> str:
    return to_iso(now_utc())


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; bare dates and naive times are UTC.

    Raises ValueError on anything unparseable — callers turn that into a
    validation refusal at creation time so garbage never lands in a contract.
    """
    s = str(value).strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
