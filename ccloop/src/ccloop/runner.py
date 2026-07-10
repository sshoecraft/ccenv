"""The relay loop.

Spawns ``claude -p`` repeatedly, streams each session's output live,
summarizes the transcript into the next session's prompt, and stops when
the resume file converges (missing / empty / DONE), the user interrupts,
or a death-loop guard trips.
"""

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import install, stream, summarize
from . import transcript as tx


def log(msg):
    sys.stderr.write(f"[ccloop] {msg}\n")
    sys.stderr.flush()


class CcloopError(Exception):
    """Fatal error that should abort the run with a message."""


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


DEFAULT_CUTOFF_TOKENS = 250000


def _config():
    return {
        "max_iterations": _env_int("CCLOOP_MAX_ITERATIONS", 0),
        "session_timeout": _env_int("CCLOOP_SESSION_TIMEOUT", 0),
        "permission_mode": os.environ.get("CCLOOP_PERMISSION_MODE", "bypassPermissions"),
        "model": os.environ.get("CCLOOP_MODEL", ""),
        "effort": os.environ.get("CCLOOP_EFFORT", ""),
        "settings": os.environ.get("CCLOOP_SETTINGS", ""),
        "max_budget": os.environ.get("CCLOOP_MAX_BUDGET_USD", ""),
        "claude_bin": os.environ.get("CCLOOP_CLAUDE_BIN", "claude") or "claude",
        "extra_args": os.environ.get("CCLOOP_CLAUDE_EXTRA_ARGS", ""),
        "stuck_limit": _env_int("CCLOOP_STUCK_LIMIT", 3),
        "watch_interval": _env_int("CCLOOP_WATCH_INTERVAL", 3),
        "api_error_grace": _env_int("CCLOOP_API_ERROR_GRACE", 60),
        # Transient LAUNCH-failure backoff: the child died at startup without
        # ever producing a transcript (model endpoint/gateway not ready). Retry
        # forever by default (limit 0 = unlimited), waiting launch_backoff
        # seconds, doubling each attempt, capped at launch_backoff_max.
        "launch_retry_limit": _env_int("CCLOOP_LAUNCH_RETRY_LIMIT", 0),
        "launch_backoff": _env_int("CCLOOP_LAUNCH_BACKOFF", 5),
        "launch_backoff_max": _env_int("CCLOOP_LAUNCH_BACKOFF_MAX", 120),
    }


def _gen_uuid():
    return str(uuid.uuid4())


def runs_dir(project_root=None):
    root = Path(project_root) if project_root else Path(os.getcwd()).resolve()
    return root / ".ccloop" / "runs"


def _first_token(text):
    if not text or not text.strip():
        return ""
    return text.lstrip().split()[0]


def _criteria_path(run_dir):
    return Path(run_dir) / "criteria.md"


def _criteria_met_path(run_dir):
    return Path(run_dir) / "criteria-met"


def _has_criteria(run_dir):
    p = _criteria_path(run_dir)
    if not p.is_file():
        return False
    try:
        return bool(p.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def converged_reason(resume_file):
    """Reason string if the run signals convergence, else None.

    Two convergence modes, picked by whether ``<run-dir>/criteria.md``
    exists and is non-empty:

    - Criteria mode: ``<run-dir>/criteria-met`` first token == YES.
    - Legacy mode: DONE in the resume file (missing / empty also count).
    """
    p = Path(resume_file)
    run_dir = p.parent

    if _has_criteria(run_dir):
        marker = _criteria_met_path(run_dir)
        if marker.is_file():
            try:
                tok = _first_token(marker.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                tok = ""
            if tok.upper().rstrip(":") == "YES":
                return "criteria-met=YES"
        return None

    if not p.exists():
        return "missing resume file"
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing resume file"
    if not txt.strip():
        return "empty resume file"
    if _first_token(txt).upper().rstrip(":")[:4] == "DONE":
        return "DONE marker"
    return None


PREAMBLE_LEGACY = """You are running inside ccloop, a relay-loop wrapper that hands work between
fresh Claude Code sessions as context fills. This is session {iter} of the
current run.

IMPORTANT — how to stop:

The only legitimate way to end the task is to run this Bash command once
the task is verifiably complete:

    echo DONE > "$CCLOOP_RESUME_FILE"

A Stop hook is active: if you try to end a turn without having written
DONE first, it will block the stop and re-feed "keep going" so you
continue working. This is intentional — it prevents the common failure
mode where a session stops mid-task and sits idle.

Therefore:

- Do NOT write DONE unless the task is actually finished and verified.
  Lying to escape the loop just wastes work; the wrapper trusts you.
- Do NOT pause to ask clarifying questions. Make a reasonable choice
  and proceed; the wrapper has no human to answer them.
- If you are genuinely blocked on something you cannot resolve, document
  the blocker in a brief text turn, write DONE, and stop — the next
  iteration or the user will pick it up.

If context starts filling before the task is done, just stop normally —
the wrapper will summarize your transcript and hand off to a fresh
session automatically.

---

"""


PREAMBLE_CRITERIA = """You are running inside ccloop, a relay-loop wrapper that hands work between
fresh Claude Code sessions as context fills. This is session {iter} of the
current run.

IMPORTANT — this run has explicit success criteria:

<BEGIN CRITERIA>
{criteria}
<END CRITERIA>

These criteria are your ONLY exit condition.

YOU HAVE UNLIMITED CONTEXT AND UNLIMITED SESSIONS.

ccloop is wrapping your session. When your context fills, ccloop will:
  1. summarize this session's transcript into a resume file
  2. spawn a fresh Claude Code session with that summary as its prompt
  3. pass through the same criteria so the new session continues the work
The new session inherits the project state, the criteria, and a digest
of what you've already learned and tried. You can take as many sessions
as the problem needs. There is no session cap, no turn cap, no time cap.

Because of this, NEVER:

- propose to "kill the wrapper", "end the session", or "exit so a fresh
  session can take over". The wrapper handles relay automatically.
- ask the user "what should I do?" or offer them options. This is
  autonomous — there is no human in the loop.
- claim the work "requires a fresh session", "needs multi-day effort
  outside this session's scope", or "is architectural and out of scope".
  Session scope is irrelevant; the work is in scope by definition.
- write YES dishonestly to escape the loop. The criteria are checked;
  lying wastes downstream work.

The Stop hook is active. Every time you try to end a turn it asks:
HAVE YOU MET THE CRITERIA? If YES, write YES to the marker:

    echo YES > "{marker}"

Only on cited, third-party-verifiable evidence that EVERY criterion is
met.

If NO, keep working. Pick a new angle: read more of the code, write a
targeted diagnostic, generate a minimal reproducer, consult any external
analysis tools available to you, form a new hypothesis and test it.
Then return to the criteria.

---

"""


def _build_prompt(resume_file, iteration):
    body = Path(resume_file).read_text(encoding="utf-8", errors="replace")
    run_dir = Path(resume_file).parent
    if _has_criteria(run_dir):
        criteria = _criteria_path(run_dir).read_text(encoding="utf-8", errors="replace").strip()
        marker = str(_criteria_met_path(run_dir))
        return PREAMBLE_CRITERIA.format(iter=iteration, criteria=criteria, marker=marker) + body
    return PREAMBLE_LEGACY.format(iter=iteration) + body


def _build_command(cfg, session_id, prompt_file=None, interactive=False):
    # The prompt is always injected via --append-system-prompt-file, keeping it
    # out of /proc/<pid>/cmdline so `pgrep -f` or `pkill -f` from inside the
    # session can't match its own parent wrapper.
    cmd = [cfg["claude_bin"]]
    if not interactive:
        cmd.append("-p")
    cmd += ["--session-id", session_id, "--permission-mode", cfg["permission_mode"]]
    if not interactive:
        # stream-json is parsed for live output; the interactive TUI renders
        # itself, so we leave its output untouched.
        cmd += ["--verbose", "--output-format", "stream-json"]
    if prompt_file:
        cmd += ["--append-system-prompt-file", str(prompt_file)]
    if cfg["model"]:
        cmd += ["--model", cfg["model"]]
    if cfg["effort"]:
        cmd += ["--effort", cfg["effort"]]
    if cfg["settings"]:
        cmd += ["--settings", cfg["settings"]]
    if cfg["max_budget"]:
        cmd += ["--max-budget-usd", cfg["max_budget"]]
    if cfg["extra_args"]:
        cmd += cfg["extra_args"].split()
    if interactive:
        # Interactive mode needs a minimal prompt on argv to start the session;
        # the real task comes from --append-system-prompt-file.
        cmd.append("begin")
    return cmd


def _session_env(cfg, run_id, session_id, resume_file, transcript_file):
    env = dict(os.environ)
    env["CCLOOP_RUN_ID"] = run_id
    env["CCLOOP_SESSION_ID"] = session_id
    env["CCLOOP_RESUME_FILE"] = str(resume_file)
    env["CCLOOP_TRANSCRIPT_PATH"] = str(transcript_file)
    env["DISABLE_AUTO_COMPACT"] = "1"

    # The whole point of ccloop is that the Stop hook keeps blocking until the
    # task is actually done. Claude Code's harness has a separate safety cap
    # (CLAUDE_CODE_STOP_HOOK_BLOCK_CAP, default 9) that overrides the hook
    # after N consecutive blocks — directly hostile to ccloop's purpose.
    # Default to unlimited; CCLOOP_STOP_HOOK_BLOCK_CAP=-1 means never cap.
    # A user who explicitly sets CLAUDE_CODE_STOP_HOOK_BLOCK_CAP in their
    # own env wins (we don't overwrite).
    if "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP" not in os.environ:
        try:
            cap = int(os.environ.get("CCLOOP_STOP_HOOK_BLOCK_CAP", "-1"))
        except ValueError:
            cap = -1
        env["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"] = str(2**31 - 1) if cap < 0 else str(cap)
    return env


def run_session(cmd, env, out_path, timeout):
    """Spawn a session, stream output live, return (exit_code, formatter).

    The child runs in its own process group; SIGINT kills the whole group
    (escalating to SIGKILL on a second Ctrl-C) and re-raises
    KeyboardInterrupt so the loop can stop and preserve state.

    The real prompt is injected via --append-system-prompt-file (in cmd).
    We pipe a minimal "begin" via stdin so -p runs non-interactively —
    nothing meaningful for pgrep/pkill to match.
    """
    fmt = stream.StreamFormatter()
    interrupted = {"count": 0}

    with open(out_path, "w", encoding="utf-8") as raw_log:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=env,
        )
        try:
            proc.stdin.write("begin")
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None

        def handle_sigint(signum, frame):
            interrupted["count"] += 1
            sig = signal.SIGKILL if interrupted["count"] > 1 else signal.SIGTERM
            if pgid is not None:
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    pass

        old_handler = signal.signal(signal.SIGINT, handle_sigint)

        timer = None
        if timeout and timeout > 0 and pgid is not None:
            def on_timeout():
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    time.sleep(5)
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            timer = threading.Timer(timeout, on_timeout)
            timer.daemon = True
            timer.start()

        try:
            for line in proc.stdout:
                raw_log.write(line)
                raw_log.flush()
                for disp in fmt.feed(line):
                    print(disp, flush=True)
            proc.wait()
        finally:
            if timer is not None:
                timer.cancel()
            signal.signal(signal.SIGINT, old_handler)

        if interrupted["count"] > 0:
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise KeyboardInterrupt

    return proc.returncode, fmt


def run_session_interactive(cmd, env, session_id, halt_file, transcript_file=None,
                            poll=3.0, api_error_grace=60):
    """Run the real Claude TUI with inherited terminal; return (exit, relayed).

    A background thread relays the session to a fresh one when ANY of three
    signals appears:

    - ``halt_file`` — the ``keepgoing`` Stop hook writes this sentinel when a
      turn ends with context at or above the run's cutoff (an *early* relay
      knob; the hook decides at a natural turn boundary).
    - the context wall — Claude Code injects a synthetic ``Prompt is too
      long`` turn into the transcript when the window fills (auto-compact is
      disabled), then idles forever waiting for ``/compact``. The wrapper
      can't see the TUI screen, but it can see that transcript event, so the
      watcher polls ``transcript_file`` for it. This is the deterministic
      guarantee that a misconfigured/absent cutoff can never wedge the run
      against the hard wall.
    - an API-error wedge — a turn that aborts on a transport/API error commits
      a non-wall ``isApiErrorMessage`` turn and then idles at the prompt
      (no relay, no Stop event). The watcher detects it via
      ``tx.last_api_error`` and relays once the same error has sat at the tail
      for ``api_error_grace`` seconds (0 disables), letting Claude Code's own
      retry go first. Recovery is the proven relay path: ``_build_prompt``
      reads ``resume.md`` with no model call, so a fresh session restarts from
      last-good state + broker/journal reconcile even mid-outage.
    """
    import termios

    relayed = {"flag": False}
    stop = threading.Event()
    # Tracks an unchanged, non-wall API-error turn sitting at the transcript
    # tail and how long it has been there. We relay only once it has persisted
    # ``api_error_grace`` seconds, so a blip Claude Code retries past resets
    # this and never triggers a relay.
    api_err = {"text": None, "since": None}

    proc = subprocess.Popen(cmd, env=env)  # inherits this process's std fds
    pid = proc.pid

    def watcher():
        while not stop.wait(poll):
            have_tx = transcript_file is not None and Path(transcript_file).is_file()
            wall = have_tx and tx.hit_context_wall(transcript_file)

            wedged = False
            if api_error_grace > 0 and have_tx and not wall:
                err = tx.last_api_error(transcript_file)
                if err is None:
                    api_err["text"] = None
                    api_err["since"] = None
                else:
                    if err != api_err["text"]:
                        api_err["text"] = err
                        api_err["since"] = time.time()
                    wedged = (time.time() - api_err["since"]) >= api_error_grace

            if halt_file.exists() or wall or wedged:
                relayed["flag"] = True
                if wall:
                    why = "context wall hit ('Prompt is too long')"
                elif wedged:
                    why = f"API-error wedge ({(api_err['text'] or '')[:60]!r})"
                else:
                    why = "context-stop signalled by hook"
                log(f"{why} — relaying to a fresh session")
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                return

    wt = threading.Thread(target=watcher, daemon=True)
    wt.start()

    # The TUI owns the terminal (raw mode handles Ctrl-C/Escape itself);
    # ignore SIGINT in the wrapper so a stray ^C can't kill the loop here.
    old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        saved_term = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, ValueError, OSError):
        saved_term = None

    try:
        proc.wait()
    finally:
        stop.set()
        wt.join(timeout=1)
        signal.signal(signal.SIGINT, old_sigint)
        if saved_term is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_term)
            except (termios.error, ValueError, OSError):
                pass

    if relayed["flag"] and proc.poll() is None:
        try:
            time.sleep(1)
            proc.kill()
        except ProcessLookupError:
            pass

    return proc.returncode, relayed["flag"]


def _confirm_relaunch():
    """Ask whether to relaunch, with the terminal guaranteed to be in cooked
    mode (the TUI may have left it raw on exit, which would swallow input)."""
    # Best-effort: force a sane terminal state before reading a line. Without
    # this the TUI's raw-mode leftovers can eat keystrokes including Enter.
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
    except (OSError, ValueError):
        pass
    # Make sure Ctrl-C is escapable here too, in case the interactive runner
    # left SIGINT ignored.
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass
    try:
        ans = input("[ccloop] Relaunch a fresh session? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes")


def _link_transcript(transcript_file, transcripts_dir, iteration):
    dest = Path(transcripts_dir) / f"session-{iteration}.jsonl"
    try:
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(transcript_file)
    except OSError:
        pass


def _write_cutoff(run_dir, cutoff_tokens, overwrite):
    """Persist the per-run cutoff in ``<run-dir>/cutoff``.

    ``overwrite=True`` always rewrites the file (new run, or resume with an
    explicit ``--cutoff``). ``overwrite=False`` only writes when the file is
    absent — that's the "resume without --cutoff" path; the existing value
    must win so the run's threshold doesn't silently reset.
    """
    cutoff_file = Path(run_dir) / "cutoff"
    if not overwrite and cutoff_file.is_file():
        return
    value = cutoff_tokens if cutoff_tokens is not None else DEFAULT_CUTOFF_TOKENS
    cutoff_file.write_text(f"{int(value)}\n", encoding="utf-8")


def _setup_new_run(task, criteria="", cutoff_tokens=None):
    run_id = _gen_uuid()
    run_dir = runs_dir() / run_id
    (run_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    (run_dir / "task.md").write_text(task + "\n", encoding="utf-8")
    (run_dir / "resume.md").write_text(task + "\n", encoding="utf-8")
    (run_dir / "sessions.log").write_text("", encoding="utf-8")
    # criteria.md is always written (empty if no criteria) so resumes can
    # see "criteria intentionally empty" vs "old run from before the flag".
    (run_dir / "criteria.md").write_text((criteria or "").strip() + "\n", encoding="utf-8")
    _write_cutoff(run_dir, cutoff_tokens, overwrite=True)
    log(f"starting run {run_id}")
    log(f"state at {run_dir}")
    if (criteria or "").strip():
        log("criteria gate active — stop allowed only on criteria-met=YES")
    return run_id, run_dir


def _setup_resume(run_id, cutoff_tokens=None):
    run_dir = runs_dir() / run_id
    if not run_dir.is_dir():
        raise CcloopError(f"run not found: {run_dir}")
    if not (run_dir / "task.md").is_file():
        raise CcloopError(f"missing task.md in {run_dir}")
    if not (run_dir / "resume.md").is_file():
        raise CcloopError(f"missing resume.md in {run_dir}")
    _write_cutoff(run_dir, cutoff_tokens, overwrite=cutoff_tokens is not None)
    log(f"resuming run {run_id}")
    return run_id, run_dir


def loop(run_id, run_dir, ensure_hook=True, interactive=False, model=None):
    cfg = _config()
    if model:
        # --model flag wins over the CCLOOP_MODEL env var.
        cfg["model"] = model
    run_dir = Path(run_dir)
    resume_file = run_dir / "resume.md"
    task_file = run_dir / "task.md"
    sessions_log = run_dir / "sessions.log"
    transcripts_dir = run_dir / "transcripts"
    task = task_file.read_text(encoding="utf-8")

    import shutil
    if shutil.which(cfg["claude_bin"]) is None and not os.path.isfile(cfg["claude_bin"]):
        raise CcloopError(f"claude binary not found: {cfg['claude_bin']}")

    if ensure_hook:
        _ensure_hook()

    if interactive:
        log("interactive mode — you drive the Claude TUI; ccloop relays on "
            "exit or when context fills")

    existing = sessions_log.read_text(encoding="utf-8").count("\n") if sessions_log.exists() else 0
    start_iter = existing
    iteration = existing
    stuck = 0

    try:
        while True:
            iteration += 1

            if cfg["max_iterations"] > 0 and iteration > start_iter + cfg["max_iterations"]:
                log(f"max iterations ({cfg['max_iterations']}) reached without convergence")
                return 1

            reason = converged_reason(resume_file)
            if reason:
                log(f"converged: {reason} (after {iteration - 1} sessions)")
                return 0

            # The handoff prompt is built once per session number; it does not
            # change across launch-failure retries (resume.md is untouched).
            prompt_text = _build_prompt(resume_file, iteration)
            prompt_file = run_dir / f"session-{iteration}.prompt"
            prompt_file.write_text(prompt_text, encoding="utf-8")

            # Spawn session `iteration`, retrying transient LAUNCH failures with
            # increasing backoff. A launch failure = the child exits nonzero
            # WITHOUT ever producing a transcript: it never reached the model
            # (endpoint/gateway down, connection refused, an auth blip at
            # connect). That is transient infrastructure, not the agent failing
            # to make progress — so ccloop waits and retries autonomously
            # instead of burning a no-progress strike or (interactive) stopping
            # to ask a human. Retries stay WITHIN this session number: only a
            # session that actually ran advances the count and is summarized.
            launch_fails = 0
            while True:
                session_id = _gen_uuid()
                transcript_file = tx.transcript_path(session_id)
                cmd = _build_command(
                    cfg, session_id,
                    prompt_file=prompt_file,
                    interactive=interactive,
                )
                env = _session_env(cfg, run_id, session_id, resume_file, transcript_file)
                halt_file = run_dir / f"halt-{session_id}"

                log(f"── session {iteration} ── id={session_id}")
                start = time.time()
                relayed = False
                if interactive:
                    exit_code, relayed = run_session_interactive(
                        cmd, env, session_id, halt_file,
                        transcript_file=transcript_file,
                        poll=cfg["watch_interval"],
                        api_error_grace=cfg["api_error_grace"],
                    )
                else:
                    exit_code, fmt = run_session(
                        cmd, env, run_dir / f"session-{iteration}.out",
                        cfg["session_timeout"],
                    )
                    # "Prompt is too long" = the context window is full. Two
                    # cases, distinguished by whether this session did any real
                    # work:
                    #   - real assistant turns > 0  → the window filled
                    #     MID-session (the wall). Relay to a fresh session;
                    #     summarize() hands off what was done — the whole point
                    #     of ccloop.
                    #   - zero real turns           → the FED prompt itself was
                    #     too big to even start. Relaying the same oversized
                    #     handoff would just fail again, so abort with guidance.
                    if fmt.saw_prompt_too_long:
                        did_work = (
                            transcript_file.is_file()
                            and tx.assistant_turns(transcript_file) >= 1
                        )
                        if did_work:
                            log("context wall hit ('Prompt is too long') — "
                                "relaying to a fresh session")
                        else:
                            raise CcloopError(
                                "session prompt exceeds the model context window "
                                "('Prompt is too long'). The resume file is too large to "
                                f"hand off. Inspect/trim {resume_file} or narrow the task, "
                                "then resume with: ccloop --resume-run " + run_id
                            )
                duration = time.time() - start
                log(f"session {iteration} ended exit={exit_code} duration={duration:.0f}s")

                try:
                    halt_file.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

                have_transcript = transcript_file.is_file()

                # Launch failure: nonzero exit, no transcript, and NOT an
                # intentional watcher relay (a relay always leaves a transcript).
                # The session never started — back off and retry rather than
                # mislabel it no-progress or stop to ask a human.
                if exit_code != 0 and not have_transcript and not relayed:
                    launch_fails += 1
                    limit = cfg["launch_retry_limit"]
                    if limit and launch_fails >= limit:
                        raise CcloopError(
                            f"session {iteration} failed to launch {launch_fails} "
                            f"times without ever starting (exit={exit_code}, no "
                            f"transcript). The claude binary ({cfg['claude_bin']}) "
                            "or its model endpoint looks unreachable — check "
                            f"{run_dir}/session-{iteration}.out, then resume with: "
                            f"ccloop --resume-run {run_id}"
                        )
                    delay = min(
                        cfg["launch_backoff"] * (2 ** (launch_fails - 1)),
                        cfg["launch_backoff_max"],
                    )
                    log(
                        f"session {iteration} never started (exit={exit_code}, no "
                        "transcript) — the claude binary or its model endpoint "
                        f"isn't ready. Retry {launch_fails} in {delay}s "
                        "(Ctrl-C to stop)"
                    )
                    time.sleep(delay)
                    continue

                break

            # One sessions.log line per session that ACTUALLY ran — the line
            # count drives resume numbering, so absorbed launch-failure retries
            # must never inflate it.
            with open(sessions_log, "a", encoding="utf-8") as fh:
                fh.write(session_id + "\n")

            if have_transcript:
                _link_transcript(transcript_file, transcripts_dir, iteration)
            else:
                log(f"WARNING: no transcript at {transcript_file}")

            # Did Claude write a convergence signal during the session?
            reason = converged_reason(resume_file)
            if reason:
                log(f"converged: {reason} (signalled during session {iteration})")
                return 0

            # Death-loop guard 2: consecutive sessions with no real work.
            productive = have_transcript and tx.assistant_turns(transcript_file) >= 1
            if productive:
                stuck = 0
            else:
                stuck += 1
                log(f"no-progress session ({stuck}/{cfg['stuck_limit']})")
                if stuck >= cfg["stuck_limit"]:
                    raise CcloopError(
                        f"{stuck} consecutive sessions made no progress — "
                        "aborting to avoid an infinite loop. Check the "
                        f"session-N.out logs in {run_dir}"
                    )

            # Summarize transcript → resume.md (atomic).
            if have_transcript:
                try:
                    new_resume = summarize.summarize(
                        transcript_file, task, run_id, iteration
                    )
                    tmp = resume_file.with_suffix(".md.tmp")
                    tmp.write_text(new_resume, encoding="utf-8")
                    os.replace(tmp, resume_file)
                    log("resume.md updated from transcript")
                except OSError as exc:
                    log(f"WARNING: summarize failed ({exc}); keeping prior resume.md")
            else:
                log("WARNING: no transcript; keeping prior resume.md")

            # Interactive: a watcher relay (context hit the hard threshold)
            # continues automatically; a plain user exit asks first, so
            # quitting the TUI doesn't trap you in an endless relaunch.
            if interactive and not relayed and not _confirm_relaunch():
                log(f"stopping at your request — resume preserved at {resume_file}")
                return 0

            time.sleep(1)
    except KeyboardInterrupt:
        log("interrupt received — terminating session")
        log(f"resume file preserved at: {resume_file}")
        return 130


def _ensure_hook():
    """Self-register all ccloop hooks (guard + keepgoing) in user settings."""
    try:
        status = install.ensure_registered()
        if status in ("added", "updated"):
            log(f"ccloop hooks {status} in {install.default_settings_path()}")
    except (ValueError, OSError) as exc:
        raise CcloopError(
            f"unable to register ccloop hooks in {install.default_settings_path()}: "
            f"{exc}. Re-run with --no-hook to proceed without them."
        )


# ── run / resume / list / prune entry points ─────────────────────────────


def cmd_run(criteria, task, ensure_hook=True, interactive=False, cutoff_tokens=None,
            model=None):
    run_id, run_dir = _setup_new_run(task, criteria=criteria, cutoff_tokens=cutoff_tokens)
    return loop(run_id, run_dir, ensure_hook=ensure_hook, interactive=interactive,
                model=model)


def cmd_resume(run_id, ensure_hook=True, interactive=False, cutoff_tokens=None,
               model=None):
    run_id, run_dir = _setup_resume(run_id, cutoff_tokens=cutoff_tokens)
    return loop(run_id, run_dir, ensure_hook=ensure_hook, interactive=interactive,
                model=model)


def _status_of(run_dir):
    run_dir = Path(run_dir)
    if _has_criteria(run_dir):
        marker = _criteria_met_path(run_dir)
        if not marker.is_file():
            return "active"
        try:
            tok = _first_token(marker.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return "active"
        return "done" if tok.upper().rstrip(":") == "YES" else "active"
    resume = run_dir / "resume.md"
    if not resume.exists():
        return "missing"
    txt = resume.read_text(encoding="utf-8", errors="replace")
    if not txt.strip():
        return "empty"
    if _first_token(txt).upper().rstrip(":")[:4] == "DONE":
        return "done"
    return "active"


def cmd_list():
    rd = runs_dir()
    if not rd.is_dir():
        print(f"no runs in {rd}")
        return 0
    print(f"{'RUN-ID':<36}  {'SESSIONS':<8}  {'STATUS':<9}  TASK")
    for d in sorted(rd.iterdir()):
        if not d.is_dir():
            continue
        slog = d / "sessions.log"
        sessions = slog.read_text(encoding="utf-8").count("\n") if slog.exists() else 0
        status = _status_of(d)
        task = "(no task.md)"
        tf = d / "task.md"
        if tf.is_file():
            for line in tf.read_text(encoding="utf-8", errors="replace").split("\n"):
                if line.strip():
                    task = line[:80]
                    break
        print(f"{d.name:<36}  {sessions:<8}  {status:<9}  {task}")
    return 0


def cmd_prune(force=False):
    rd = runs_dir()
    if not rd.is_dir():
        print(f"no runs in {rd}")
        return 0
    converged = [
        d for d in sorted(rd.iterdir())
        if d.is_dir() and _status_of(d) in ("done", "empty")
    ]
    if not converged:
        print("no converged runs to prune")
        return 0
    if not force:
        print("would delete (use --force to actually delete):")
        for d in converged:
            print(f"  {d.name}")
        print(f"{len(converged)} run(s) match")
        return 0
    import shutil
    for d in converged:
        shutil.rmtree(d)
        print(f"deleted: {d.name}")
    print(f"{len(converged)} run(s) pruned")
    return 0
