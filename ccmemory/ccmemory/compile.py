"""LLM-compiled knowledge articles.

Pattern from claude-memory-compiler: raw per-session lessons accumulate
faster than humans can curate them. Periodically run a compiler that reads
N raw memories and produces structured, cross-referenced knowledge articles
under ``compiled/<topic>.md``. The raw inputs stay where they are — the
compiled article is an additional, denser entry.

Implementation: shells out to ``claude -p`` (Claude Code headless mode).
This keeps ccmemory provider-agnostic — anyone with claude on PATH can run
it. No API keys, no model selection in code. If ``claude`` is not on PATH
or returns non-zero, the call fails loudly (not silently).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .store import Store


COMPILER_PROMPT = """\
You are compiling raw per-session memory files into a single dense knowledge
article. Read the inputs below. Produce ONE markdown article that:

1. Identifies the central topic the inputs share.
2. Extracts every decision, lesson, and recurring failure mode — deduplicated
   and chronologically ordered when timing matters.
3. Cross-references the source sessions using the literal slugs you see in
   the input (e.g. `[[sess79_lessons]]`).
4. Ends with a YAML frontmatter block at the very TOP of the article in this
   exact format:

   ---
   name: compiled-<short-kebab-topic>
   description: one-line summary suitable for a memory index (<150 chars)
   metadata:
     type: project
   tags: [compiled, <topic-tags>]
   ---

5. Be terse. Engineering prose, no platitudes, no headers like "## Summary".

Output ONLY the article markdown. No explanation before or after."""


def _resolve_claude_bin() -> str | None:
    return shutil.which(os.environ.get("CCMEMORY_CLAUDE_BIN", "claude"))


def _build_input(memories: list[dict]) -> str:
    chunks = []
    for m in memories:
        body = Path(m["path"]).read_text(encoding="utf-8", errors="replace")
        chunks.append(f"\n========== {m['name']} ({m.get('type') or '-'}, age {m['age_days']:.0f}d) ==========\n{body}\n")
    return "\n".join(chunks)


def compile_directory(
    memory_dir: Path,
    *,
    topic: str | None = None,
    max_inputs: int = 20,
    dry_run: bool = False,
    output_subdir: str = "compiled",
) -> dict[str, Any]:
    with Store(memory_dir) as s:
        s.reindex()
        if topic:
            picks = s.search(topic, limit=max_inputs)
        else:
            # No topic → grab the newest project-typed memories.
            picks = []
            for row in s.db.execute(
                "SELECT name, path, type, description, mtime FROM mem "
                "WHERE type = 'project' ORDER BY mtime DESC LIMIT ?",
                (max_inputs,),
            ):
                import time
                age_days = max(0.0, (time.time() - row["mtime"]) / 86400.0)
                picks.append({
                    "name": row["name"], "path": row["path"], "type": row["type"],
                    "description": row["description"], "age_days": age_days, "score": 0.0, "bm25": 0.0,
                })

    if not picks:
        return {"status": "no-inputs", "topic": topic}

    input_text = _build_input(picks)
    full_prompt = f"{COMPILER_PROMPT}\n\n========== INPUTS ==========\n{input_text}"

    if dry_run:
        return {
            "status": "dry-run",
            "topic": topic,
            "input_count": len(picks),
            "input_names": [p["name"] for p in picks],
            "prompt_bytes": len(full_prompt),
        }

    claude_bin = _resolve_claude_bin()
    if not claude_bin:
        return {"status": "error", "error": "claude CLI not found on PATH (set CCMEMORY_CLAUDE_BIN to override)"}

    try:
        proc = subprocess.run(
            [claude_bin, "-p", full_prompt],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "claude -p timed out after 600s"}

    if proc.returncode != 0:
        return {"status": "error", "returncode": proc.returncode, "stderr": proc.stderr[-2000:]}

    article = proc.stdout.strip()
    if not article.startswith("---"):
        return {"status": "error", "error": "compiler output missing frontmatter", "head": article[:400]}

    # Pull name from frontmatter
    fm_end = article.find("---", 4)
    front = article[4:fm_end] if fm_end > 0 else ""
    name = None
    for line in front.splitlines():
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip()
            break
    if not name:
        name = "compiled-" + (topic.replace(" ", "-") if topic else "untitled")

    out_dir = memory_dir / output_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.md"
    out_path.write_text(article + ("\n" if not article.endswith("\n") else ""), encoding="utf-8")

    # Reindex so the new article is searchable immediately.
    with Store(memory_dir) as s:
        s.reindex()

    return {
        "status": "ok",
        "name": name,
        "path": str(out_path),
        "input_count": len(picks),
        "input_names": [p["name"] for p in picks],
        "article_bytes": len(article),
    }
