"""ccmemory CLI — minimal ops surface.

ccmemory is fundamentally an MCP server + hooks. The "CLI" here exists only
because:

  1. The MCP server has to be launched somehow (``ccmemory mcp``)
  2. Hook entries in settings.json invoke shell commands (``ccmemory hook X``)
  3. There need to be manual install/uninstall/status escape hatches

Day-to-day memory access goes through the MCP tools (``memory_search``,
``memory_get``, ``memory_write``, etc.), not these subcommands. There are
no search/get/stats subcommands here on purpose.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import installer
from . import hooks as hooks_mod
from . import migrate as migrate_mod
from . import paths


def cmd_install(args):
    settings = Path(args.settings) if args.settings else None
    status = installer.ensure_registered(settings_path=settings)
    target = settings or installer.default_settings_path()
    print(f"ccmemory: {status} in {target}")
    for event, matcher, sub in installer.HOOKS:
        m = f" (matcher: {matcher})" if matcher else ""
        print(f"  {event}{m}: {installer.hook_command(sub)}")


def cmd_uninstall(args):
    settings = Path(args.settings) if args.settings else None
    changed = installer.uninstall(settings_path=settings)
    target = settings or installer.default_settings_path()
    print(f"ccmemory: {'removed from' if changed else 'not present in'} {target}")


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


def cmd_compile(args):
    from . import compile as compile_mod
    import json
    d = paths.resolve_memory_dir()
    if not d:
        sys.exit("error: no memory dir resolvable from cwd (set CCMEMORY_DIR or run inside a project)")
    result = compile_mod.compile_status(d, topic=args.topic, max_inputs=args.max)
    print(json.dumps(result, indent=2, default=str))


def cmd_migrate(args):
    import json
    source = Path(args.from_) if args.from_ else None
    target = Path(args.to) if args.to else None
    result = migrate_mod.migrate(
        source=source,
        target=target,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )
    print(json.dumps(result.as_dict(), indent=2))
    sys.exit(0 if result.status == "ok" else 1)


def cmd_where(args):
    print(f"project_root:   {paths.project_root()}")
    print(f"project_dir:    {paths.project_memory_dir()}")
    print(f"legacy_dir:     {paths.legacy_memory_dir()}")
    print(f"resolved (use): {paths.resolve_memory_dir()}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccmemory", description="ccmemory — MCP memory server with hooks")
    p.add_argument("--version", action="version", version=f"ccmemory {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("mcp", help="run MCP server (stdio); the primary entry point")
    pm.set_defaults(func=cmd_mcp)

    ph = sub.add_parser("hook", help="hook entry point (invoked by Claude Code via settings.json)")
    ph.add_argument("name", choices=list(hooks_mod.HANDLERS))
    ph.set_defaults(func=cmd_hook)

    pi = sub.add_parser("install", help="manually register ccmemory hooks (normally autoinstalled on MCP boot)")
    pi.add_argument("--settings", help="path to settings.json (default: ~/.claude/settings.json)")
    pi.set_defaults(func=cmd_install)

    pu = sub.add_parser("uninstall", help="remove ccmemory hooks from settings.json")
    pu.add_argument("--settings")
    pu.set_defaults(func=cmd_uninstall)

    psr = sub.add_parser("status", help="show install state")
    psr.set_defaults(func=cmd_status)

    pc = sub.add_parser("compile", help="report the memory-compaction backlog + candidate inputs (compile via the compile-memories skill — no claude -p)")
    pc.add_argument("--topic")
    pc.add_argument("--max", type=int, default=20)
    pc.set_defaults(func=cmd_compile)

    pmg = sub.add_parser("migrate", help="copy legacy memory into project-local .ccmemory/ (also runs automatically on MCP boot)")
    pmg.add_argument("--from", dest="from_", help="source dir (default: legacy ~/.claude/projects/<slug>/memory)")
    pmg.add_argument("--to", help="target dir (default: <project_root>/.ccmemory/)")
    pmg.add_argument("--dry-run", action="store_true")
    pmg.add_argument("--overwrite", action="store_true", help="replace existing .ccmemory/ content")
    pmg.set_defaults(func=cmd_migrate)

    pw = sub.add_parser("where", help="show resolved project root + memory dir")
    pw.set_defaults(func=cmd_where)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
