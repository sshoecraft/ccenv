"""ccprospect MCP server — exposes the store as MCP tools.

Uses ``ccenvmcp`` (the stdlib-only, Python 3.9+ MCP shim) like ccmemory.
``dispatch()`` is module-level (not a closure) so the whole tool surface is
testable without ccenvmcp installed.

Tools:
  - prospect_file(title, intention, predicate, expires, expect?, bucket?, evidence?)
  - prospect_inbox()
  - prospect_ack(id, disposition, resolution?, note?, evidence?, next_review?)
  - prospect_amend(id, title?, intention?, predicate?, expires?, expect?, bucket?, evidence?)
  - prospect_list(status?)
  - prospect_get(id)
  - prospect_report()

Read tools on a project with no ``.ccprospect/`` return an informative
empty result — the store directory is only created by the first
prospect_file, so browsing never litters a repo.
"""

from __future__ import annotations

import json
import logging
import sys

from . import paths
from .contracts import ContractError
from .predicates import PredicateError
from .store import Store, StoreError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("ccprospect-mcp")

NO_STORE = ("no .ccprospect/ store in this directory yet — prospect_file(...) "
            "creates it on first use")


def _text(s: str) -> list[dict]:
    return [{"type": "text", "text": s}]


def _json(obj) -> list[dict]:
    return _text(json.dumps(obj, indent=2, default=str))


def dispatch(name: str, arguments: dict) -> list[dict]:
    if name not in SCHEMAS:
        return _text(f"unknown tool: {name}")
    try:
        if name == "prospect_file":
            d = paths.resolve_prospect_dir(must_exist=False)
            store = Store(d, create=True)
            contract = store.create(
                title=arguments.get("title") or "",
                intention=arguments.get("intention") or "",
                predicate=arguments.get("predicate") or {},
                expires=arguments.get("expires") or "",
                expect=arguments.get("expect"),
                bucket=arguments.get("bucket"),
                evidence=arguments.get("evidence"),
            )
            return _json({"id": contract.id, "title": contract.title,
                          "expires": contract.expires,
                          "predicate": contract.predicate,
                          "path": str(contract.path)})

        d = paths.resolve_prospect_dir()
        if d is None:
            if name in ("prospect_inbox", "prospect_list", "prospect_report"):
                empty = {"prospect_inbox": {"fired": [], "due": [], "expiring_soon": [],
                                            "pending_count": 0, "active": 0, "note": NO_STORE},
                         "prospect_list": [],
                         "prospect_report": {"total": 0, "note": NO_STORE}}
                return _json(empty[name])
            return _text(NO_STORE)
        store = Store(d)

        if name == "prospect_inbox":
            import os
            allow_probes = not os.environ.get("CCPROSPECT_NO_PROBES")
            return _json(store.inbox(evaluate_first=True, allow_probes=allow_probes))

        if name == "prospect_ack":
            return _json(store.ack(
                arguments.get("id") or "",
                arguments.get("disposition") or "",
                resolution=arguments.get("resolution"),
                note=arguments.get("note"),
                evidence=arguments.get("evidence"),
                next_review=arguments.get("next_review"),
            ))

        if name == "prospect_amend":
            return _json(store.amend(
                arguments.get("id") or "",
                title=arguments.get("title"),
                intention=arguments.get("intention"),
                predicate=arguments.get("predicate"),
                expires=arguments.get("expires"),
                expect=arguments.get("expect"),
                bucket=arguments.get("bucket"),
                evidence=arguments.get("evidence"),
            ))

        if name == "prospect_list":
            return _json(store.list_all(arguments.get("status")))

        if name == "prospect_get":
            return _json(store.get(arguments.get("id") or ""))

        if name == "prospect_report":
            return _json(store.report())

        raise RuntimeError(f"tool {name} declared in SCHEMAS but not dispatched")
    except (StoreError, PredicateError, ContractError) as e:
        # Refusals are the mechanism, not failures — return them verbatim.
        return _text(f"refused: {e}")
    except Exception as e:
        log.exception("tool %s failed", name)
        return _text(f"{type(e).__name__}: {e}")


PREDICATE_SCHEMA = {
    "type": "object",
    "description": ("typed predicate, exactly one: {type: 'at', time} | "
                    "{type: 'session_start'} | {type: 'path_exists', path, negate?} | "
                    "{type: 'path_changed', path} | "
                    "{type: 'cmd_ok'|'cmd_fail', run, timeout?, min_interval?} | "
                    "{type: 'cmd_match', run, regex, timeout?, min_interval?}. "
                    "Refused if already true at creation. Relative paths / probe cwd "
                    "= the project startup dir. timeout ≤ 10s; min_interval (default "
                    "3600s) rate-limits probing."),
    "properties": {
        "type": {"type": "string",
                 "enum": ["at", "session_start", "path_exists", "path_changed",
                          "cmd_ok", "cmd_fail", "cmd_match"]},
        "time": {"type": "string", "description": "ISO-8601, for 'at'"},
        "path": {"type": "string", "description": "for path_exists/path_changed"},
        "negate": {"type": "boolean", "description": "path_exists: fire on disappearance"},
        "run": {"type": "string", "description": "shell probe, for cmd_*"},
        "regex": {"type": "string", "description": "for cmd_match"},
        "timeout": {"type": "integer", "description": "probe seconds, 1..10"},
        "min_interval": {"type": "integer", "description": "min seconds between probes"},
    },
    "required": ["type"],
}

SCHEMAS = {
    "prospect_file": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "short index-line title (≤80 chars)"},
            "intention": {"type": "string",
                          "description": "what the waking session should DO when this fires — the prospective payload"},
            "predicate": PREDICATE_SCHEMA,
            "expires": {"type": "string", "description": "ISO-8601 expiry — REQUIRED, nothing is open-ended"},
            "expect": {"type": "string",
                       "description": "optional falsifiable claim; adding it makes this a FORECAST that resolves hit/miss"},
            "bucket": {"type": "integer", "enum": [20, 40, 60, 80],
                       "description": "optional probability bucket (only with expect; no 50 on purpose)"},
            "evidence": {"type": "string", "description": "why this exists (provenance note)"},
        },
        "required": ["title", "intention", "predicate", "expires"],
    },
    "prospect_inbox": {"type": "object", "properties": {}},
    "prospect_ack": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "contract id (p-0007, a unique prefix, or bare number)"},
            "disposition": {"type": "string",
                            "enum": ["done", "keep", "defer", "cancel_attention", "resolve"]},
            "resolution": {"type": "string", "enum": ["hit", "miss", "unresolvable"],
                           "description": "required when disposition=resolve"},
            "note": {"type": "string", "description": "required for cancel_attention (the reason)"},
            "evidence": {"type": "string",
                         "description": "commit hash / PR URL / path proving the disposition"},
            "next_review": {"type": "string", "description": "ISO-8601, required for defer"},
        },
        "required": ["id", "disposition"],
    },
    "prospect_amend": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "contract to supersede"},
            "title": {"type": "string"},
            "intention": {"type": "string"},
            "predicate": PREDICATE_SCHEMA,
            "expires": {"type": "string"},
            "expect": {"type": "string"},
            "bucket": {"type": "integer", "enum": [20, 40, 60, 80]},
            "evidence": {"type": "string"},
        },
        "required": ["id"],
    },
    "prospect_list": {
        "type": "object",
        "properties": {
            "status": {"type": "string",
                       "enum": ["active", "open", "fired", "acked", "deferred", "closed", "all"],
                       "description": "filter (default all)"},
        },
    },
    "prospect_get": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
    "prospect_report": {"type": "object", "properties": {}},
}

DESCRIPTIONS = {
    "prospect_file": ("File a prospective-memory contract: an intention (or forecast, with "
                      "expect+bucket) bound to a typed predicate and an expiry. IMMUTABLE once "
                      "filed. Refused while fired items sit unacknowledged, when the predicate "
                      "is already true, or past the attention caps. Declining to file is always "
                      "legal — never file filler."),
    "prospect_inbox": ("Evaluate all open predicates NOW and return fired items (with "
                       "mechanically observed values), due reviews, expiring-soon, and counts. "
                       "pending_count is the number needing a disposition."),
    "prospect_ack": ("Submit exactly one disposition for an inbox item: done | keep | "
                     "defer(+next_review) | cancel_attention(+note) | resolve(+hit|miss|"
                     "unresolvable). Cancelling attention never cancels the outcome — the "
                     "contract still resolves counterfactually."),
    "prospect_amend": ("Supersede a contract with a revised successor (new id; predecessor "
                       "linked). The original is closed for attention but still resolves "
                       "counterfactually at its original expiry — revision never erases a "
                       "forecast."),
    "prospect_list": "List prospects (derived state summaries), optionally filtered by status.",
    "prospect_get": "Full detail for one prospect: contract fields, derived state, event history.",
    "prospect_report": ("The factual aging/calibration report: counts by state, ack latency, "
                        "hit/miss by probability bucket, counterfactual outcomes of cancelled/"
                        "superseded items. Denominators always shown; no scores, no advice."),
}


def build_app():
    """Construct the ccenvmcp app with all tools registered."""
    from ccenvmcp import FastMCP

    app = FastMCP("ccprospect")

    def register(tool_name: str):
        @app.tool(name=tool_name, description=DESCRIPTIONS[tool_name],
                  schema=SCHEMAS[tool_name])
        def handler(**kwargs):
            return dispatch(tool_name, kwargs)
        return handler

    for tool_name in SCHEMAS:
        register(tool_name)

    return app


def serve() -> int:
    # Autoinstall hooks on MCP server boot — ccprospect's real entry point,
    # same choke point ccmemory uses.
    from . import installer
    installer.autoinstall_quiet()

    app = build_app()
    app.run()
    return 0
