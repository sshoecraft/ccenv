"""Command-line entry point and dispatch.

Kept deliberately light at import time: the ``guard`` no-op path (the
common case when the hook fires outside a ccloop run) returns before any
heavy modules are imported.
"""

import re
import sys
from pathlib import Path

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

USAGE = """ccloop — relay-loop wrapper for Claude Code

Usage:
  ccloop "<criteria>" "<task>"  start a new run with success criteria
                                (empty criteria "" = legacy DONE-marker mode)
  ccloop --resume-run <run-id>  resume an existing run
  ccloop --list                 list runs in the current project
  ccloop --prune [--force]      delete converged runs; dry-run by default
  ccloop install [--uninstall]  manually (un)register the guard hook
  ccloop guard                  PostToolUse hook (invoked by Claude Code)
  ccloop keepgoing              Stop hook — re-feed model until criteria met
                                (invoked by Claude Code; no-op outside ccloop)
  ccloop --help                 show this help

Tip: load criteria from a file with shell substitution, e.g.
  ccloop "$(cat criteria.md)" "fix the silent dirent loss bug"

Options:
  --no-hook                     skip guard-hook registration for this run
  -i, --interactive             force the interactive Claude TUI (relay on exit)
  --headless                    run autonomously via headless `claude -p`
                                REQUIRES --accept-api-cost (headless bills the
                                metered Agent SDK credit at API rates, not your sub)
  --accept-api-cost             acknowledge headless API billing; required with --headless
                                (default: interactive TUI on a TTY. With NO TTY and no
                                --headless --accept-api-cost, ccloop errors out rather
                                than silently running metered headless `claude -p`.)
  --cutoff=N                    relay cutoff in thousands of tokens (default: 250)
                                (0 = no cutoff — keep going until the session window fills)
  --model=NAME                  model for the spawned claude sessions — an alias
                                (opus, sonnet, haiku) or a full model id; passed
                                to `claude --model`, overrides CCLOOP_MODEL

Environment variables:
  CCLOOP_MAX_ITERATIONS    hard cap on sessions per run (default: 0 = unlimited)
  CCLOOP_SESSION_TIMEOUT   SIGTERM a session after N seconds (default: 0 = none)
  CCLOOP_WATCH_INTERVAL    interactive halt-sentinel poll seconds (default: 3)
  CCLOOP_STUCK_LIMIT       consecutive no-progress sessions before abort (default: 3)
  CCLOOP_LAUNCH_RETRY_LIMIT  cap retries when a session never starts, no transcript
                             (default: 0 = retry forever until the endpoint returns)
  CCLOOP_LAUNCH_BACKOFF      first launch-retry wait, seconds; doubles each try (default: 5)
  CCLOOP_LAUNCH_BACKOFF_MAX  ceiling on the launch-retry wait, seconds (default: 120)
  CCLOOP_MAX_CONTINUES     cap keepgoing re-feeds per session (default: 0 = unlimited)
  CCLOOP_STOP_HOOK_BLOCK_CAP  override Claude Code's Stop hook cap (default: -1 = unlimited)
  CCLOOP_PERMISSION_MODE   default: bypassPermissions
  CCLOOP_MODEL             override model (--model flag wins over this)
  CCLOOP_EFFORT            override effort level
  CCLOOP_SETTINGS          path/JSON for claude --settings
  CCLOOP_MAX_BUDGET_USD    per-session cost cap
  CCLOOP_CLAUDE_BIN        claude binary to invoke (default: claude)
  CCLOOP_CLAUDE_EXTRA_ARGS extra args appended to every claude invocation

State: .ccloop/runs/<run-id>/ in the current directory.
"""


def _run(fn, *args, **kwargs):
    from .runner import CcloopError
    try:
        return fn(*args, **kwargs)
    except CcloopError as exc:
        print(f"ccloop: {exc}", file=sys.stderr)
        return 1


def _extract_value_flag(argv, flag, what):
    """Pop ``<flag>=V`` / ``<flag> V`` from argv.

    Returns ``(new_argv, value, error)``. ``value`` is the raw string, or
    None when the flag was not given (last occurrence wins). ``error`` is
    a usage-error message string when the value is missing; the caller
    should print it and return 2.
    """
    out = []
    value = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith(flag + "="):
            value = a.split("=", 1)[1]
        elif a == flag:
            if i + 1 >= len(argv):
                return argv, None, f"{flag} requires a value ({what})"
            i += 1
            value = argv[i]
        else:
            out.append(a)
        i += 1
    return out, value, None


def _extract_cutoff(argv):
    """Pop ``--cutoff=N`` / ``--cutoff N`` from argv.

    Returns ``(new_argv, cutoff_tokens, error)``. ``cutoff_tokens`` is the
    parsed value in raw tokens (N * 1000), or None when the flag was not
    given. ``error`` is a usage-error message string when N is missing or
    not a positive int; the caller should print it and return 2.
    """
    argv, raw, error = _extract_value_flag(argv, "--cutoff", "thousands of tokens")
    if error or raw is None:
        return argv, None, error
    try:
        n = int(raw)
    except ValueError:
        return argv, None, f"--cutoff: not an integer: {raw!r}"
    if n < 0:
        return argv, None, f"--cutoff: must be a non-negative integer (got {n})"
    # 0 is the explicit "no cutoff" sentinel — keep going until the
    # session window fills. Any positive N is N thousand tokens.
    return argv, n * 1000, None


def _extract_model(argv):
    """Pop ``--model=NAME`` / ``--model NAME`` from argv.

    Returns ``(new_argv, model, error)``. ``model`` is the name/alias to
    pass through to ``claude --model`` (it wins over CCLOOP_MODEL), or
    None when the flag was not given.
    """
    argv, model, error = _extract_value_flag(argv, "--model", "model name or alias")
    if error is None and model is not None and not model.strip():
        return argv, None, "--model requires a value (model name or alias)"
    return argv, model, error


def _cmd_install(args):
    from . import install
    settings_path = None
    action = "install"
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--uninstall":
            action = "uninstall"
        elif a == "--project":
            settings_path = Path(".claude/settings.json")
        elif a == "--settings":
            i += 1
            if i >= len(args):
                print("ccloop install: --settings requires a path", file=sys.stderr)
                return 2
            settings_path = Path(args[i])
        else:
            print(f"ccloop install: unknown option: {a}", file=sys.stderr)
            return 2
        i += 1

    try:
        if action == "uninstall":
            changed = install.uninstall(settings_path)
            print("removed ccloop hooks" if changed else "no ccloop hooks to remove")
        else:
            status = install.ensure_registered(settings_path=settings_path)
            target = settings_path or install.default_settings_path()
            print(f"ccloop hooks {status}: {target}")
            for event, sub in install.HOOKS.items():
                print(f"  {event}: {install.hook_command(sub)}")
    except (ValueError, OSError) as exc:
        print(f"ccloop install: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Fast no-op gate for hooks — return before importing anything heavy.
    if argv[:1] == ["guard"]:
        from . import guard
        return guard.main(argv[1:])
    if argv[:1] == ["keepgoing"]:
        from . import keepgoing
        return keepgoing.main(argv[1:])

    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        from . import __version__
        print(__version__)
        return 0

    ensure_hook = True
    if "--no-hook" in argv:
        ensure_hook = False
        argv = [a for a in argv if a != "--no-hook"]

    force_interactive = False
    if "--interactive" in argv or "-i" in argv:
        force_interactive = True
        argv = [a for a in argv if a not in ("--interactive", "-i")]
    want_headless = False
    if "--headless" in argv:
        want_headless = True
        argv = [a for a in argv if a != "--headless"]
    accept_api_cost = False
    if "--accept-api-cost" in argv:
        accept_api_cost = True
        argv = [a for a in argv if a != "--accept-api-cost"]

    def _resolve_interactive():
        """Decide interactive vs headless for a run/resume. Returns
        ``(interactive_bool, exit_code)``; exit_code is None on success or a
        usage-error code (2) the caller should return after we've printed why.

        Policy: headless ``claude -p`` is NEVER selected implicitly. It bills
        against the metered Agent SDK credit at full API rates (the June 2026
        billing change moved headless / Agent SDK usage off the subscription),
        so it requires an explicit, acknowledged opt-in: BOTH --headless and
        --accept-api-cost. Auto-detect picks the interactive TUI on a TTY and
        REFUSES on no TTY rather than silently falling back to metered -p.
        """
        if force_interactive and want_headless:
            print("ccloop: --interactive and --headless are mutually exclusive",
                  file=sys.stderr)
            return None, 2
        if want_headless:
            if not accept_api_cost:
                print(
                    "ccloop: --headless runs `claude -p`, which bills against the metered\n"
                    "  Agent SDK credit at API rates (not your subscription). Re-run with\n"
                    "  BOTH flags to confirm you accept that cost:\n"
                    "    ccloop --headless --accept-api-cost ...",
                    file=sys.stderr,
                )
                return None, 2
            return False, None
        if force_interactive:
            return True, None
        if sys.stdin.isatty() and sys.stdout.isatty():
            return True, None
        print(
            "ccloop: no TTY detected, and headless mode was not authorized.\n"
            "  ccloop no longer silently falls back to headless `claude -p` — that bills\n"
            "  against the metered Agent SDK credit at API rates. Choose one:\n"
            "    • run inside a terminal for the interactive TUI (uses your subscription), or\n"
            "    • pass --headless --accept-api-cost to run autonomously at API cost.",
            file=sys.stderr,
        )
        return None, 2

    argv, cutoff_tokens, cutoff_err = _extract_cutoff(argv)
    if cutoff_err:
        print(f"ccloop: {cutoff_err}", file=sys.stderr)
        return 2

    argv, model, model_err = _extract_model(argv)
    if model_err:
        print(f"ccloop: {model_err}", file=sys.stderr)
        return 2

    if argv and argv[0] == "install":
        return _cmd_install(argv[1:])

    from . import runner

    if argv[0] == "--list":
        return runner.cmd_list()

    if argv[0] == "--prune":
        rest = argv[1:]
        force = False
        if rest == ["--force"]:
            force = True
        elif rest:
            print(f"ccloop: unknown option after --prune: {rest[0]}", file=sys.stderr)
            return 2
        return runner.cmd_prune(force=force)

    if argv[0] == "--resume-run":
        if len(argv) < 2:
            print("ccloop: --resume-run requires a run-id", file=sys.stderr)
            return 2
        run_id = argv[1]
        if not UUID_RE.match(run_id):
            print(
                f"ccloop: invalid run-id: must be a lowercase UUID (got: {run_id})",
                file=sys.stderr,
            )
            return 2
        interactive, err = _resolve_interactive()
        if err is not None:
            return err
        return _run(runner.cmd_resume, run_id, ensure_hook=ensure_hook,
                    interactive=interactive, cutoff_tokens=cutoff_tokens,
                    model=model)

    if argv[0].startswith("-"):
        print(f"ccloop: unknown option: {argv[0]}", file=sys.stderr)
        return 2

    if len(argv) < 2:
        print(
            "ccloop: two arguments required — criteria and task.\n"
            "  ccloop \"<criteria>\" \"<task>\"\n"
            "  ccloop \"\" \"<task>\"           # legacy mode (no criteria gate)\n"
            "  ccloop \"$(cat crit.md)\" \"<task>\"  # criteria from file\n"
            "Or use --resume-run / --list / --prune; see --help.",
            file=sys.stderr,
        )
        return 2
    if len(argv) > 2:
        print(
            f"ccloop: too many positional arguments ({len(argv)}); expected 2 "
            "(criteria, task). Quote each argument.",
            file=sys.stderr,
        )
        return 2
    criteria, task = argv[0], argv[1]
    if not task.strip():
        print("ccloop: task argument is empty", file=sys.stderr)
        return 2
    interactive, err = _resolve_interactive()
    if err is not None:
        return err
    return _run(runner.cmd_run, criteria, task, ensure_hook=ensure_hook,
                interactive=interactive, cutoff_tokens=cutoff_tokens,
                model=model)


if __name__ == "__main__":
    sys.exit(main())
