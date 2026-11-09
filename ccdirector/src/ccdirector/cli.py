"""ccdirector — a project-manager Claude session and the workers it directs.

    ccdirector [--cutoff N] start [PROJECT] [--goal TEXT|FILE] [--workers N]
               [--cutoff N] [--effort L] [--model M]
               [--worker-cutoff N] [--worker-effort L] [--worker-model M] [--plan-only]
        Resolve the goal (the --goal directive, or the goal-finder); run the
        director's planning session until it has an accepted work plan; then run
        up to --workers worker sessions on the plan while the director steers,
        until the project's gate passes or the director reports it is stuck.
        --cutoff/--effort/--model are the director's; --worker-* the workers'.
        Cutoffs are ccloop's, in thousands of tokens (0 = relay only when the
        context window fills). --plan-only stops after the plan.
    ccdirector goal [PROJECT]
        Run the goal-finder alone and print what it found.
    ccdirector check [PROJECT] [--build B]
        The done-check: the project's own gate. Exit 0 done, 1 not done,
        2 nothing defines done for this project.
    ccdirector status [PROJECT]
        The plan's items, their states, and which workers are running.

  Used by the director session:
    ccdirector plan submit|show|schema|done   submit (stdin), show, describe, or
                                              end the planning run
    ccdirector wait [--timeout S]             block until something happens
    ccdirector goal-done                      end the steering run if the gate passes
    ccdirector goal-stuck REASON              end it when nothing can progress

  Used by worker sessions:
    ccdirector item next [--wait]             the worker's next item, WAIT or NONE
    ccdirector item done ID                   run the item's check
    ccdirector item park ID --reason TEXT     give an item back to the director
    ccdirector hook pre|post                  the per-file edit lock (Claude Code hooks)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import (ccproject, conflicts, editlock, goalfinder, hooks, plan, progress, session, work,
               workers)

#: How many times ccdirector reopens a director run that ended without doing
#: what the run is for, before it gives up and reports.
ATTEMPTS = 3
#: `wait` and `item next --wait` block inside one Bash tool call, which Claude
#: Code caps at 10 minutes.
WAIT_SECONDS = 540

PLAN_CRITERIA = (
    "ccdirector has ACCEPTED your work plan (`ccdirector plan show` prints it), and its "
    "work items cover every criterion of the goal that is not yet green on the project's "
    "criteria board. This is the exit condition of your planning run only: it does not "
    "mean any board criterion is green or any project work is done. When it holds, run "
    "`ccdirector plan done`; it writes the marker file, which your deny rules stop you "
    "writing yourself."
)

STEER_CRITERIA = (
    "Either `ccdirector goal-done` has confirmed that the project's gate passes, or "
    "`ccdirector goal-stuck` has recorded why nothing can progress without the user. Each "
    "writes ccloop's marker itself when it accepts; you cannot write it, and nothing else "
    "ends this run."
)


def project(path: str | None) -> Path:
    """The project: the argument, else CCDIRECTOR_ROOT (set in every director and worker
    session, whose shell may have changed directory), else the current directory.

    State written while ccdirector shipped inside ccteam lives in `.ccteam/director/`;
    it is moved to `.ccdirector/` the first time ccdirector meets the project, because
    ccenv's uninstaller purges `.ccteam/` when it removes ccteam."""
    root = Path(path or os.environ.get("CCDIRECTOR_ROOT") or ".").expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"ccdirector: {root} is not a directory")
    old, new = root / ".ccteam" / "director", plan.folder(root)
    if old.is_dir() and not new.exists():
        old.rename(new)
        print(f"ccdirector: moved {old} to {new}", file=sys.stderr)
    return root


def directive(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.read_text().strip()
    return value.strip()


def save_goal(root: Path, goal: goalfinder.Goal, count: int,
              round_limit: int = progress.DEFAULT_ROUND_LIMIT) -> Path:
    record = goal.as_dict()
    record["workers"] = count
    record["round_limit"] = round_limit
    record["resolved"] = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    plan.folder(root).mkdir(parents=True, exist_ok=True)
    path = plan.folder(root) / "goal.json"
    plan.atomic(path, json.dumps(record, indent=2) + "\n")
    return path


def load_goal(root: Path) -> dict:
    path = plan.folder(root) / "goal.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def gate_line(root: Path, build: str | None) -> str:
    gate = ccproject.gate_command(root, build)
    return (f"Done is decided by `{' '.join(gate)}` exiting 0." if gate else
            "Done will be decided by `ccproject_criteria.py --gate` once the criteria "
            "above are on the board.")


def plan_task(root: Path, goal: goalfinder.Goal, count: int, build: str | None) -> str:
    lines = ["This is your PLANNING run.", "", f"Project root: {root}",
             f"Workers available: {count}", "",
             "## Goal", "", goal.goal, "", f"Source: {goal.source}"]
    if goal.reason:
        lines += [f"Why this source: {goal.reason}"]
    lines += [""]
    if goal.existing:
        lines += ["Board criteria the goal rests on: " + ", ".join(goal.existing), ""]
    if goal.proposed:
        lines += ["## Criteria to add to the board", "",
                  "Add each with ccproject_criteria.py add before planning:", "",
                  "```json", json.dumps(goal.proposed, indent=2), "```", ""]
    if goal.evidence:
        lines += ["## Evidence the goal was based on", ""] + [f"- {e}" for e in goal.evidence] + [""]
    lines += ["## Done", "", gate_line(root, build), "Use that command as the plan's `gate`.", ""]
    existing = plan.load(root)
    issues = plan.problems(existing) if existing else []
    if issues:
        lines += ["## The plan on file", "",
                  "This run exists only because the plan on file cannot be used as it "
                  "stands. Read it with `ccdirector plan show`, repair exactly these "
                  "problems, keep every other item and field as written, and submit it. "
                  "Do not write a new plan.", ""] + [f"- {i}" for i in issues] + [""]
    return "\n".join(lines)


def steer_task(root: Path, goal: goalfinder.Goal, count: int, build: str | None) -> str:
    return "\n".join([
        "This is your STEERING run. Your plan is accepted and up to "
        f"{count} workers are carrying it out now.", "",
        f"Project root: {root}", "", "## Goal", "", goal.goal, "",
        "## Done", "", gate_line(root, build), "",
        "Follow the steering section of your instructions: `ccdirector wait`, act on what "
        "it reports, and end only through `ccdirector goal-done` or `ccdirector goal-stuck`.",
        "", "Where things stand now:", "", work.summary(root), ""])


def signal_run(root: Path) -> tuple[bool, str]:
    """Write the calling session's ccloop criteria-met marker. Sessions cannot write
    it themselves: it is under the project root, where their deny rules apply."""
    run_id = os.environ.get("CCLOOP_RUN_ID", "").strip()
    if not run_id:
        return False, "CCLOOP_RUN_ID is not set; this only works inside a ccloop run"
    run_dir = session.runs_folder(root) / run_id
    if not run_dir.is_dir():
        return False, f"no ccloop run folder {run_dir}"
    marker = run_dir / "criteria-met"
    marker.write_text("YES\n")
    return True, str(marker)


def director_flags(args) -> dict:
    return dict(headless=args.headless, model=args.model, cutoff=args.cutoff, effort=args.effort)


def report_user_stop(run: session.Run) -> int:
    """The user ended the director's run. Nothing reopens it and no later stage starts,
    whatever the plan or the gate says."""
    print(f"ccdirector: stopped at your request (run {run.run_id}); `ccdirector start` "
          "picks up from the plan on file", file=sys.stderr)
    return run.returncode


def planning(root: Path, goal: goalfinder.Goal, args) -> int:
    run = session.start(root, PLAN_CRITERIA, plan_task(root, goal, args.workers, args.build),
                        **director_flags(args))
    for attempt in range(ATTEMPTS):
        if run.folder is None:
            print(f"ccdirector: ccloop exited {run.returncode} without starting a run",
                  file=sys.stderr)
            return run.returncode or 1
        if run.stopped_by_user:
            return report_user_stop(run)
        current = plan.load(root)
        issues = plan.problems(current) if current else ["no plan was submitted"]
        if not issues:
            print(f"ccdirector: plan accepted: {plan.folder(root) / 'plan.md'}", file=sys.stderr)
            return 0
        if run.returncode != 0 or attempt == ATTEMPTS - 1:
            print(f"ccdirector: the director's run {run.run_id} ended (exit "
                  f"{run.returncode}) without an accepted plan:", file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            return 1
        run = session.resume(root, run, "Your run ended, but there is no accepted work "
                             "plan. Problems:\n" + "\n".join(f"- {i}" for i in issues),
                             **director_flags(args))
    return 1


def stuck_record(root: Path) -> dict | None:
    path = plan.folder(root) / "stuck.json"
    return json.loads(path.read_text()) if path.is_file() else None


def steering(root: Path, goal: goalfinder.Goal, args) -> int:
    run = session.start(root, STEER_CRITERIA, steer_task(root, goal, args.workers, args.build),
                        **director_flags(args))
    for attempt in range(ATTEMPTS):
        if run.folder is None:
            print(f"ccdirector: ccloop exited {run.returncode} without starting a run",
                  file=sys.stderr)
            return run.returncode or 1
        if run.stopped_by_user:
            return report_user_stop(run)
        verdict = ccproject.check(root, args.build)
        if verdict.done:
            print(f"ccdirector: goal met: {verdict.gate} passes", file=sys.stderr)
            return 0
        stuck = stuck_record(root)
        if stuck:
            print(f"ccdirector: the director stopped: {stuck['reason']}", file=sys.stderr)
            return 1
        if run.returncode != 0 or attempt == ATTEMPTS - 1:
            print(f"ccdirector: the director's run {run.run_id} ended (exit {run.returncode}) "
                  f"with the gate not passing and no reason recorded", file=sys.stderr)
            return 1
        run = session.resume(root, run, "Your run ended, but the gate does not pass and you "
                             "did not record `ccdirector goal-stuck`. The gate says:\n"
                             + verdict.output[-3000:], **director_flags(args))
    return 1


def resolve_goal(root: Path, args) -> goalfinder.Goal | None:
    if args.goal:
        goal = goalfinder.Goal(found=True, goal=directive(args.goal), source="directive",
                               reason="given on the command line")
        if not goal.goal:
            sys.exit("ccdirector: --goal is empty")
        return goal
    print("ccdirector: no goal given; running the goal-finder…", file=sys.stderr)
    try:
        goal = goalfinder.find(root, timeout=args.goal_timeout, model=args.goal_model)
    except goalfinder.GoalError as exc:
        print(f"ccdirector: goal-finder failed: {exc}", file=sys.stderr)
        return None
    if not goal.found:
        print(f"ccdirector: no goal found for {root}\n  {goal.reason}", file=sys.stderr)
        return None
    print(f"ccdirector: goal ({goal.source}, ${goal.cost_usd:.2f}): {goal.goal}",
          file=sys.stderr)
    return goal


def goal_on_file(existing: dict | None) -> goalfinder.Goal | None:
    """The goal a plan on file carries, so reusing the plan needs no goal-finder."""
    if not existing:
        return None
    text = existing.get(plan.WRITTEN_FOR) or existing.get("goal")
    if not isinstance(text, str) or not text.strip():
        return None
    return goalfinder.Goal(found=True, goal=text, source=str(existing.get("source") or "plan"),
                           reason="the plan on file")


def terminate(signum, frame):
    raise SystemExit(128 + signum)


def cmd_start(args) -> int:
    root = project(args.project)
    found = conflicts.ccteam(root)
    if found:
        print("ccdirector: ccteam is configured here, and its plugin would run in the "
              "director's and every worker's session, replaying edits and locking them a "
              "second time. Remove it first (ccenv's install.sh removes it as a retired "
              "component, or `claude plugin uninstall` / `claude mcp remove ccteam`):",
              file=sys.stderr)
        for where in found:
            print(f"  - {where}", file=sys.stderr)
        return 1
    if args.workers < 1:
        sys.exit("ccdirector: --workers must be at least 1")
    if args.round_limit < 0:
        sys.exit("ccdirector: --round-limit must be 0 (no limit) or more")
    for name in ("cutoff", "worker_cutoff"):
        value = getattr(args, name)
        if value is not None and value < 0:
            sys.exit(f"ccdirector: --{name.replace('_', '-')} must be 0 or more "
                     "(thousands of tokens)")
    existing = None if args.replan else plan.load(root)
    goal = goal_on_file(existing) or resolve_goal(root, args)
    if goal is None:
        return 1
    if existing and args.goal and directive(args.goal) != goal.goal:
        print(f"ccdirector: keeping the plan on file, written for: {goal.goal}\n"
              "  --goal does not change it; add --replan to write a new plan", file=sys.stderr)
    if args.replan:
        plan.set_aside(root)
    save_goal(root, goal, args.workers, args.round_limit)
    progress.forget_pending(root)
    signal.signal(signal.SIGTERM, terminate)
    try:
        if existing and not plan.problems(existing):
            print(f"ccdirector: using the plan on file: {plan.folder(root) / 'plan.md'}",
                  file=sys.stderr)
            code = 0
        else:
            code = planning(root, goal, args)
        if code or args.plan_only:
            return code
        returned = work.reset_claims(root)
        if returned:
            print(f"ccdirector: returned items left in progress by an earlier run: "
                  f"{', '.join(returned)}", file=sys.stderr)
        stale = plan.folder(root) / "stuck.json"
        if stale.exists():
            stale.unlink()
        work.sync(root, plan.load(root))
        supervisor = workers.Supervisor(root, workers.Options(
            count=args.workers, cutoff=args.worker_cutoff, effort=args.worker_effort,
            model=args.worker_model))
        supervisor.start()
        try:
            return steering(root, goal, args)
        finally:
            print("ccdirector: stopping workers…", file=sys.stderr)
            supervisor.stop()
            print(work.summary(root), file=sys.stderr)
    except session.SessionError as exc:
        print(f"ccdirector: {exc}", file=sys.stderr)
        return 1


def cmd_goal(args) -> int:
    root = project(args.project)
    try:
        goal = goalfinder.find(root, timeout=args.timeout, model=args.model)
    except goalfinder.GoalError as exc:
        print(f"ccdirector: goal-finder failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(goal.as_dict(), indent=2))
    return 0 if goal.found else 1


def cmd_check(args) -> int:
    root = project(args.project)
    try:
        verdict = ccproject.check(root, args.build)
    except ccproject.ToolError as exc:
        print(f"ccdirector: {exc}", file=sys.stderr)
        return 2
    print(verdict.output)
    if not verdict.gate:
        return 2
    return 0 if verdict.done else 1


def cmd_status(args) -> int:
    root = project(args.project)
    print(progress.report(root) + "\n\n" + work.summary(root))
    return 0


def cmd_plan_done(root: Path) -> int:
    """End the planning run. Refused unless the accepted plan was submitted for the
    goal `ccdirector start` set, so a plan left over from an earlier goal cannot
    end the run. `start` rewrites goal.json every time, so the file's age says
    nothing about whether the goal changed."""
    current = plan.load(root)
    if current is None:
        print("REFUSED: no plan has been accepted; submit one with `ccdirector plan submit`")
        return 1
    issues = plan.problems(current)
    if issues:
        print(f"REFUSED: the stored plan has {len(issues)} problem(s); submit a fixed one:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    goal = load_goal(root).get("goal")
    if goal and current.get(plan.WRITTEN_FOR) != goal:
        print("REFUSED: the accepted plan was not submitted for the current goal; keep "
              "the items that still serve it and submit the plan for this goal")
        return 1
    ok, detail = signal_run(root)
    if not ok:
        print(f"REFUSED: {detail}")
        return 1
    print(f"DONE: wrote {detail}. The plan is accepted; no project work has been done and "
          "no board criterion has changed.")
    return 0


def cmd_plan(args) -> int:
    root = project(args.project)
    if args.action == "schema":
        print(plan.SCHEMA_TEXT)
        return 0
    if args.action == "done":
        return cmd_plan_done(root)
    if args.action == "show":
        current = plan.load(root)
        if current is None:
            print("no plan yet")
            return 1
        print(plan.render(current))
        return 0
    try:
        submitted = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"REFUSED: the plan is not valid JSON: {exc}")
        return 1
    issues = plan.problems(submitted)
    if issues:
        print(f"REFUSED: {len(issues)} problem(s); fix all of them and submit again:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    waiting = progress.consult_waiting(root)
    if waiting and not args.no_consult:
        print(f"REFUSED: {', '.join(waiting)} has not answered yet. Wait for its answer, read "
              "it, rework the plan around it, then submit.")
        return 1
    if progress.consult_due(root) and not args.no_consult:
        print(f"REFUSED: {progress.CONSULT_EVERY} replans since the last consult. Consult an "
              "advisor first (ask_astra, ask_fable or ask_gpt) and wait for its answer. If none "
              "is available, run `ccdirector plan submit --no-consult REASON`.")
        return 1
    if args.no_consult and progress.consult_due(root):
        with work.locked(root):
            work.event(root, "consult_skipped", reason=args.no_consult)
    submitted.pop(plan.WRITTEN_FOR, None)
    goal = load_goal(root).get("goal")
    if goal:
        submitted[plan.WRITTEN_FOR] = goal
    json_path, md_path = plan.write(root, submitted)
    changes = work.sync(root, submitted)
    progress.replan_submitted(root)
    print(f"ACCEPTED: {json_path} and {md_path}")
    for change in changes:
        print(f"  {change}")
    return 0


def describe(record: dict) -> str:
    kind = record.get("kind", "?")
    fields = {k: v for k, v in record.items() if k not in ("time", "kind")}
    output = fields.pop("output", None)
    text = f"{record.get('time', '')} {kind} " + " ".join(
        f"{k}={json.dumps(v) if not isinstance(v, str) else v}" for k, v in fields.items())
    if output:
        text += "\n    " + "\n    ".join(str(output).splitlines()[-15:])
    return text


def cmd_wait(args) -> int:
    root = project(args.project)
    current = plan.load(root)
    state = work.load(root)
    timeout = args.timeout
    if work.all_done(state, current) or work.stalled(state, current):
        timeout = 0
    found = work.wait_for_events(root, "director.cursor", timeout)
    print(progress.report(root) + "\n")     # first: the screen collapses all but the top lines
    for record in found:
        print(describe(record))
    if not found:
        print(f"no new events in {timeout:.0f}s")
    state = work.load(root)
    print("\n" + work.summary(root))
    if work.all_done(state, current):
        print("\nALL ITEMS DONE. Run `ccdirector goal-done`. If the gate still fails, follow "
              "what it prints.")
    elif work.stalled(state, current):
        print("\nSTALLED: nothing is in progress and nothing can be handed out. Revise the "
              "plan (answer parked items by resubmitting them, add or split items), or, if "
              "nothing can progress without the user, run `ccdirector goal-stuck REASON`.")
    return 0


REPLAN_DUE = (
    "\nREPLAN DUE: every item is done and the gate is still red, so more of the same will "
    "not fix it. Decide what the evidence says is wrong with the approach and submit the plan "
    "reworked around that, with new items for the red criteria and, in `risks`, what you "
    "changed and why. Finished items stay done."
)

CONSULT_DUE = (
    f"\nCONSULT DUE: every item is done, the gate is still red, and this is replan "
    f"{progress.CONSULT_EVERY} since the last consult. Before you submit anything: (1) consult "
    "an advisor (ask_astra, ask_fable or ask_gpt) with the red criteria, what this run has "
    "tried and what each attempt measured; (2) wait for its answer and read it; (3) submit the "
    "plan reworked around it, with new items for the red criteria and, in `risks`, what the "
    "advisor said and what you changed. Finished items stay done. `plan submit` refuses until "
    "the answer is back; if no advisor is available, use `plan submit --no-consult REASON`."
)


def stop_at_round_limit(root: Path, verdict: ccproject.Verdict) -> int:
    """End the run the way `goal-stuck` does: `steering` reads the reason and stops the
    workers. The director is not asked, because it has never chosen to stop on its own."""
    record = progress.load(root)
    rounds = record.get("rounds_since_gain", 0)
    reason = (f"round limit: {rounds} rounds ended with every item done, the gate red and no "
              f"new criterion green ({record.get('green')} of {record.get('total')}). The plan "
              f"was extended {progress.extensions(root)} times; what was tried and decided is "
              f"under risks in {plan.folder(root) / 'plan.md'}. Start again for one more round, "
              f"or change --round-limit or use --replan. The gate says:\n{verdict.output[-1500:]}")
    ok, detail = signal_run(root)
    if not ok:
        print(f"REFUSED: {detail}")
        return 1
    plan.atomic(plan.folder(root) / "stuck.json",
                json.dumps({"reason": reason, "time": work.now()}, indent=2) + "\n")
    with work.locked(root):
        work.event(root, "round_limit", rounds=rounds, green=record.get("green"),
                   total=record.get("total"))
    print(f"STOPPED: {reason}")
    return 0


def cmd_goal_done(args) -> int:
    root = project(args.project)
    try:
        verdict = ccproject.check(root, args.build)
    except ccproject.ToolError as exc:
        print(f"REFUSED: {exc}")
        return 1
    if not verdict.done:
        closed = progress.record_refusal(root)
        print(f"REFUSED: {verdict.gate or 'no gate'} does not pass:\n{verdict.output[-4000:]}")
        if closed and progress.limit_reached(root):
            return stop_at_round_limit(root, verdict)
        if closed:
            print(CONSULT_DUE if progress.consult_due(root) else REPLAN_DUE)
        return 1
    ok, detail = signal_run(root)
    if not ok:
        print(f"REFUSED: {detail}")
        return 1
    with work.locked(root):
        work.event(root, "goal_done", gate=verdict.gate)
    print(f"DONE: {verdict.gate} passes; wrote {detail}")
    return 0


def cmd_goal_stuck(args) -> int:
    root = project(args.project)
    reason = " ".join(args.reason).strip()
    if not reason:
        print("REFUSED: give the reason: what blocks progress and what is needed from the user")
        return 1
    current = plan.load(root)
    state = work.load(root)
    if current is not None and not (work.stalled(state, current) or work.all_done(state, current)):
        print("REFUSED: work is still in progress or can still be handed out:\n"
              + work.summary(root))
        return 1
    ok, detail = signal_run(root)
    if not ok:
        print(f"REFUSED: {detail}")
        return 1
    plan.atomic(plan.folder(root) / "stuck.json",
                json.dumps({"reason": reason, "time": work.now()}, indent=2) + "\n")
    with work.locked(root):
        work.event(root, "goal_stuck", reason=reason)
    print(f"STOPPED: recorded the reason and wrote {detail}")
    return 0


def worker_name() -> str:
    name = os.environ.get("CCDIRECTOR_WORKER", "").strip()
    if not name:
        sys.exit("ccdirector: CCDIRECTOR_WORKER is not set; `item` commands are for worker sessions")
    return name


def cmd_item(args) -> int:
    root = project(args.project)
    worker = worker_name()
    if args.action == "next":
        end = time.time() + (WAIT_SECONDS if args.wait else 0)
        while True:
            kind, value = work.next_item(root, worker)
            if kind != "wait" or time.time() >= end:
                break
            time.sleep(3)
        if kind == "item":
            print("ITEM")
            print(json.dumps(value, indent=2))
            if work.consult_required(root, value["id"]):
                print("\nCONSULT FIRST: this item has been parked repeatedly with nobody asking "
                      "for help. Ask an advisor about it (ask_astra, ask_fable or ask_gpt) and "
                      "wait for the answer. `item done` is refused until you have it.")
            return 0
        if kind == "wait":
            print(f"WAIT: {value}")
            return 0
        ok, detail = signal_run(root)
        print(f"NONE: {value}" + (f"; wrote {detail}" if ok else f" ({detail})"))
        return 0
    if not args.id:
        sys.exit(f"ccdirector: item {args.action} needs an item id")
    if args.action == "done":
        progress.settle_consults(root)      # a backgrounded answer may have just arrived
        outcome, output = work.finish(root, worker, args.id)
        label = {"done": "PASSED", "failed": "FAILED", "refused": "REFUSED",
                 "parked": "PARKED"}[outcome]
        print(f"{label}: {args.id}")
        print(output)
        return 0 if outcome == "done" else 1
    reason = (args.reason or "").strip()
    if not reason:
        sys.exit("ccdirector: item park needs --reason")
    ok, detail = work.park(root, worker, args.id, reason)
    print(("PARKED: " if ok else "REFUSED: ") + detail)
    return 0 if ok else 1


def cmd_hook(args) -> int:
    if args.stage == "notify":
        return hooks.notify()
    if args.stage == "consult":
        return hooks.consult()
    return editlock.hook(args.stage)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccdirector", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    cutoff_help = ("ccloop relay cutoff for the director, in thousands of tokens "
                   "(0 = relay only when the context window fills; default: ccloop's)")
    parser.add_argument("--cutoff", type=int, help=cutoff_help)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="resolve the goal, plan it, and carry it out")
    p.add_argument("project", nargs="?")
    p.add_argument("--goal", help="the goal, as text or a file holding it")
    # SUPPRESS: given here it wins; absent, the value given before `start` stays.
    p.add_argument("--cutoff", type=int, default=argparse.SUPPRESS, help=cutoff_help)
    p.add_argument("--effort", help="reasoning effort for the director (ccloop --effort)")
    p.add_argument("--model", help="model for the director session")
    p.add_argument("--workers", type=int, default=4, help="most workers running at once")
    p.add_argument("--worker-cutoff", type=int,
                   help="ccloop relay cutoff for each worker, thousands of tokens")
    p.add_argument("--worker-effort", help="reasoning effort for each worker")
    p.add_argument("--worker-model", help="model for each worker")
    p.add_argument("--plan-only", action="store_true", help="stop once the plan is accepted")
    p.add_argument("--replan", action="store_true",
                   help="write a new plan instead of using the one on file")
    p.add_argument("--round-limit", type=int, default=progress.DEFAULT_ROUND_LIMIT,
                   help="end the run after this many rounds with every item done, the gate "
                        "red and no criterion gained (0 = no limit)")
    p.add_argument("--build", help="grade the done-check against this build")
    p.add_argument("--goal-model", help="model for the goal-finder")
    p.add_argument("--goal-timeout", type=float, default=goalfinder.DEFAULT_TIMEOUT)
    p.add_argument("--headless", action="store_true",
                   help="run the director with claude -p instead of the interactive TUI")
    p.set_defaults(handler=cmd_start)

    p = sub.add_parser("goal", help="run the goal-finder alone")
    p.add_argument("project", nargs="?")
    p.add_argument("--model")
    p.add_argument("--timeout", type=float, default=goalfinder.DEFAULT_TIMEOUT)
    p.set_defaults(handler=cmd_goal)

    p = sub.add_parser("check", help="the done-check")
    p.add_argument("project", nargs="?")
    p.add_argument("--build")
    p.set_defaults(handler=cmd_check)

    p = sub.add_parser("status", help="items, their states, and running workers")
    p.add_argument("project", nargs="?")
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser("plan", help="submit, show or describe the work plan")
    p.add_argument("action", choices=["submit", "show", "schema", "done"])
    p.add_argument("--no-consult", metavar="REASON",
                   help="submit: no advisor is available for the consult a finished round needs")
    p.add_argument("--project")
    p.set_defaults(handler=cmd_plan)

    p = sub.add_parser("wait", help="director: block until something happens")
    p.add_argument("--project")
    p.add_argument("--timeout", type=float, default=WAIT_SECONDS)
    p.set_defaults(handler=cmd_wait)

    p = sub.add_parser("goal-done", help="director: end the steering run if the gate passes")
    p.add_argument("--project")
    p.add_argument("--build")
    p.set_defaults(handler=cmd_goal_done)

    p = sub.add_parser("goal-stuck", help="director: end the steering run when nothing can "
                                          "progress without the user")
    p.add_argument("reason", nargs="+")
    p.add_argument("--project")
    p.set_defaults(handler=cmd_goal_stuck)

    p = sub.add_parser("item", help="worker: take, finish or park an item")
    p.add_argument("action", choices=["next", "done", "park"])
    p.add_argument("id", nargs="?")
    p.add_argument("--wait", action="store_true", help="next: wait up to 9 minutes for one")
    p.add_argument("--reason", help="park: what is wrong and what is needed")
    p.add_argument("--project")
    p.set_defaults(handler=cmd_item)

    p = sub.add_parser("hook", help="worker: Claude Code edit-lock hook")
    p.add_argument("stage", choices=["pre", "post", "notify", "consult"])
    p.set_defaults(handler=cmd_hook)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
