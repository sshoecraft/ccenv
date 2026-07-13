"""ccprospect CLI — minimal ops surface (ccmemory's philosophy).

ccprospect is fundamentally an MCP server + hooks. The CLI exists only
because:

  1. The MCP server has to be launched somehow (``ccprospect mcp``)
  2. Hook entries in settings.json invoke shell commands (``ccprospect hook X``)
  3. There need to be manual install/uninstall/status escape hatches
  4. ``ccprospect inbox`` / ``ccprospect report`` give a human (or a
     cron job) read-only visibility without an MCP client

Day-to-day prospect access goes through the MCP tools.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import hooks as hooks_mod
from . import installer
from . import paths


def cmd_install(args):
    settings = Path(args.settings) if args.settings else None
    status = installer.ensure_registered(settings_path=settings)
    target = settings or installer.default_settings_path()
    print(f"ccprospect: {status} in {target}")
    for event, matcher, sub in installer.HOOKS:
        m = f" (matcher: {matcher})" if matcher else ""
        print(f"  {event}{m}: {installer.hook_command(sub)}")


def cmd_uninstall(args):
    settings = Path(args.settings) if args.settings else None
    changed = installer.uninstall(settings_path=settings)
    target = settings or installer.default_settings_path()
    print(f"ccprospect: {'removed from' if changed else 'not present in'} {target}")


def cmd_status(args):
    target = installer.default_settings_path()
    print(f"settings: {target}")
    print(f"registered: {installer.is_registered()}")
    for event, matcher, sub in installer.HOOKS:
        m = f" (matcher: {matcher})" if matcher else ""
        print(f"  {event}{m} → {installer.hook_command(sub)}")


def cmd_hook(args):
    sys.exit(hooks_mod.dispatch(args.name))


def cmd_mcp(args):
    from . import mcp_server
    sys.exit(mcp_server.serve())


def _require_store():
    d = paths.resolve_prospect_dir()
    if not d:
        sys.exit(f"no prospect store yet ({paths.startup_prospect_dir()} does not exist); "
                 "the prospect_file MCP tool creates it on first use")
    return d


def cmd_inbox(args):
    from .store import Store
    import os
    d = _require_store()
    allow_probes = not (args.no_probes or os.environ.get("CCPROSPECT_NO_PROBES"))
    result = Store(d).inbox(evaluate_first=not args.no_evaluate,
                            allow_probes=allow_probes)
    print(json.dumps(result, indent=2, default=str))


def cmd_report(args):
    from .store import Store
    d = _require_store()
    print(json.dumps(Store(d).report(), indent=2, default=str))


def cmd_where(args):
    print(f"startup_dir:              {paths.startup_dir()}")
    print(f"startup_dir/.ccprospect:  {paths.startup_prospect_dir()}")
    print(f"resolved (use):           {paths.resolve_prospect_dir()}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccprospect",
                                description="ccprospect — prospective-memory MCP server with hooks")
    p.add_argument("--version", action="version", version=f"ccprospect {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("mcp", help="run MCP server (stdio); the primary entry point")
    pm.set_defaults(func=cmd_mcp)

    ph = sub.add_parser("hook", help="hook entry point (invoked by Claude Code via settings.json)")
    ph.add_argument("name", choices=list(hooks_mod.HANDLERS))
    ph.set_defaults(func=cmd_hook)

    pi = sub.add_parser("install", help="manually register ccprospect hooks (normally autoinstalled on MCP boot)")
    pi.add_argument("--settings", help="path to settings.json (default: ~/.claude/settings.json)")
    pi.set_defaults(func=cmd_install)

    pu = sub.add_parser("uninstall", help="remove ccprospect hooks from settings.json")
    pu.add_argument("--settings")
    pu.set_defaults(func=cmd_uninstall)

    psr = sub.add_parser("status", help="show install state")
    psr.set_defaults(func=cmd_status)

    pib = sub.add_parser("inbox", help="evaluate + print the inbox as JSON (read-only ops view)")
    pib.add_argument("--no-evaluate", action="store_true", help="show current state without evaluating")
    pib.add_argument("--no-probes", action="store_true", help="skip cmd probes during evaluation")
    pib.set_defaults(func=cmd_inbox)

    pr = sub.add_parser("report", help="print the factual aging/calibration report as JSON")
    pr.set_defaults(func=cmd_report)

    pw = sub.add_parser("where", help="show resolved startup dir + prospect dir")
    pw.set_defaults(func=cmd_where)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
