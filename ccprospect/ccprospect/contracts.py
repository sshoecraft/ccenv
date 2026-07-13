"""Contract files — the immutable record.

One ``.md`` file per contract under ``.ccprospect/contracts/``, named
``p-NNNN-<slug>.md``. YAML frontmatter holds ONLY immutables (id, title,
intention, predicate, expires, expect?, bucket?, evidence?, predecessor?,
created_at, session). Current state is NEVER stored here — it is derived by
folding ``events.jsonl`` (event sourcing in ccmemory's file idiom). A
PreToolUse hook blocks direct edits, so immutability is enforced by the
harness, not by convention.

IDs are short deterministic slugs (``p-0007``), not UUIDs — weak models
mangle UUIDs (proven in the aitrader thread), and short ids are
prefix-resolvable by hand.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from . import paths

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
ID_RE = re.compile(r"^p-(\d{4,})$")
TITLE_CAP = 80


class ContractError(ValueError):
    """Refusal at the contract layer (bad fields, unknown id, collision)."""


@dataclass
class Contract:
    id: str
    title: str
    intention: str
    predicate: dict
    expires: str
    created_at: str
    session: str | None = None
    expect: str | None = None
    bucket: int | None = None
    evidence: str | None = None
    predecessor: str | None = None
    path: Path | None = field(default=None, compare=False)


def slugify(title: str, cap: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:cap].rstrip("-") or "prospect"


def next_id(prospect_dir: Path) -> str:
    """Allocate the next sequential id by scanning existing contract files.

    Concurrent-session races are handled by the O_EXCL write in
    :func:`write_contract` — a collision retries with the next number.
    """
    highest = 0
    cdir = paths.contracts_dir(prospect_dir)
    if cdir.exists():
        for p in cdir.glob("p-*.md"):
            if p.name.startswith("._"):
                continue
            m = re.match(r"^p-(\d+)", p.stem)
            if m:
                highest = max(highest, int(m.group(1)))
    return f"p-{highest + 1:04d}"


def parse_contract(path: Path) -> Contract | None:
    """Parse one contract file. Returns None on files that aren't contracts
    (no frontmatter / no id) so a stray file can't crash every evaluation."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = FRONTMATTER_RE.match(raw)
    if not m:
        return None
    front = m.group(1)
    meta: dict = {}
    if yaml is not None:
        try:
            parsed = yaml.safe_load(front) or {}
            if isinstance(parsed, dict):
                meta = parsed
        except yaml.YAMLError:
            meta = {}
    if not meta:
        meta = _parse_frontmatter_fallback(front)

    cid = str(meta.get("id") or "")
    if not ID_RE.match(cid):
        return None
    predicate = meta.get("predicate")
    if not isinstance(predicate, dict):
        return None

    bucket = meta.get("bucket")
    try:
        bucket = int(bucket) if bucket is not None else None
    except (TypeError, ValueError):
        bucket = None

    def opt(key: str) -> str | None:
        v = meta.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return Contract(
        id=cid,
        title=str(meta.get("title") or path.stem),
        intention=str(meta.get("intention") or "").strip(),
        predicate=predicate,
        expires=str(meta.get("expires") or ""),
        created_at=str(meta.get("created_at") or ""),
        session=opt("session"),
        expect=opt("expect"),
        bucket=bucket,
        evidence=opt("evidence"),
        predecessor=opt("predecessor"),
        path=path,
    )


def _parse_frontmatter_fallback(front: str) -> dict:
    # PyYAML-less best effort: flat `k: v` lines plus one nested 1-deep map
    # (enough for a predicate of scalar fields). Multiline block scalars are
    # not recoverable here — PyYAML is a declared dependency; this only
    # cushions a broken environment.
    out: dict = {}
    current_key: str | None = None
    for line in front.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] not in " \t" and ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                current_key = k
                out[k] = {}
            else:
                out[k] = v.strip("'\"")
                current_key = None
        elif current_key and ":" in line:
            k, _, v = line.partition(":")
            if isinstance(out.get(current_key), dict):
                out[current_key][k.strip()] = v.strip().strip("'\"")
    return out


def write_contract(prospect_dir: Path, fields: dict) -> Contract:
    """Write a new immutable contract file (O_EXCL — never overwrites).

    ``fields`` must already be validated; this layer only serializes. On an
    id collision (two sessions allocating concurrently) the caller re-allocs
    and retries.
    """
    if yaml is None:
        raise ContractError("PyYAML is required to write contracts (pip install pyyaml)")
    cdir = paths.contracts_dir(prospect_dir)
    cdir.mkdir(parents=True, exist_ok=True)

    cid = fields["id"]
    ordered = {}
    for key in ("id", "title", "intention", "predicate", "expires", "expect",
                "bucket", "evidence", "predecessor", "created_at", "session"):
        value = fields.get(key)
        if value is not None:
            ordered[key] = value

    front = yaml.safe_dump(ordered, default_flow_style=False, sort_keys=False,
                           allow_unicode=True, width=88)
    content = "---\n" + front + "---\n"
    path = cdir / f"{cid}-{slugify(fields['title'])}.md"

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)

    contract = parse_contract(path)
    if contract is None:
        raise ContractError(f"contract {cid} failed to round-trip — not written correctly")
    return contract


def load_all(prospect_dir: Path) -> dict[str, Contract]:
    """All contracts by id, skipping AppleDouble sidecars and non-contracts."""
    out: dict[str, Contract] = {}
    cdir = paths.contracts_dir(prospect_dir)
    if not cdir.exists():
        return out
    for p in sorted(cdir.glob("*.md")):
        if p.name.startswith("._"):
            continue
        c = parse_contract(p)
        if c is not None:
            out[c.id] = c
    return out


def resolve_id(contracts: dict[str, Contract], fragment: str) -> str:
    """Resolve an exact id, a bare number ('7' → p-0007), or a unique prefix."""
    frag = str(fragment).strip()
    if frag in contracts:
        return frag
    if re.fullmatch(r"\d+", frag):
        candidate = f"p-{int(frag):04d}"
        if candidate in contracts:
            return candidate
    matches = [cid for cid in contracts if cid.startswith(frag)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ContractError(f"no prospect matches id '{fragment}'")
    raise ContractError(f"id '{fragment}' is ambiguous: {', '.join(sorted(matches))}")
