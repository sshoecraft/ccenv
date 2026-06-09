"""
MEMORY.md index generator.

The bloat problem (52KB index loaded every session, descriptions of 2200+ chars)
is structural: nothing enforces the "one-line pointer" rule, so under ccloop
the model appends full session summaries. Fix: stop hand-maintaining MEMORY.md.
Generate it from each file's frontmatter `description:` field with a hard cap.

This module produces MEMORY.md deterministically. A Stop hook should call it.
"""

from __future__ import annotations

from pathlib import Path

from .store import Store

DEFAULT_DESC_CAP = 150  # chars per index line description
DEFAULT_FILE_CAP = 12_000  # total bytes MEMORY.md allowed to reach


def generate(memory_dir: Path, *, desc_cap: int = DEFAULT_DESC_CAP) -> str:
    store = Store(memory_dir)
    try:
        store.reindex()
        sections: dict[str, list[tuple[str, str, str]]] = {}
        for row in store.all_memories():
            mtype = row["type"] or "other"
            desc = (row["description"] or "").strip().replace("\n", " ")
            if len(desc) > desc_cap:
                desc = desc[: desc_cap - 1].rstrip() + "…"
            name = row["name"]
            path = Path(row["path"]).name
            sections.setdefault(mtype, []).append((name, path, desc))
    finally:
        store.close()

    order = ["user", "feedback", "project", "reference", "other"]
    out: list[str] = []
    for section in order + [k for k in sections if k not in order]:
        entries = sections.get(section)
        if not entries:
            continue
        out.append(f"## {section}")
        for name, path, desc in sorted(entries):
            line = f"- [{name}]({path})"
            if desc:
                line += f" — {desc}"
            out.append(line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write(memory_dir: Path, *, desc_cap: int = DEFAULT_DESC_CAP, file_cap: int = DEFAULT_FILE_CAP) -> dict:
    content = generate(memory_dir, desc_cap=desc_cap)
    truncated = False
    if len(content.encode("utf-8")) > file_cap:
        content = content.encode("utf-8")[:file_cap].decode("utf-8", errors="ignore")
        content = content.rsplit("\n", 1)[0] + "\n# [TRUNCATED — file_cap exceeded; tighten desc_cap or prune memories]\n"
        truncated = True
    target = memory_dir / "MEMORY.md"
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "bytes": len(content.encode("utf-8")), "truncated": truncated}
