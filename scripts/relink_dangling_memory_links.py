#!/usr/bin/env python3
"""Turn wikilinks to memories that no longer exist into plain `slug` text.

memory_delete rewrites every link to a note it deletes as `` `slug` ``, so the
citation survives as provenance and nothing links to a missing note. A link
still pointing at a missing memory came from a deletion that went around
memory_delete. mxfs had 60 of them, in 6 articles written about ten days before
they were found. This applies memory_delete's rewrite to each one.

Usage:
    python3 scripts/relink_dangling_memory_links.py <memory-dir> [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ccmemory"))
from ccmemory.store import WIKILINK_RE, _parse_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("memory_dir", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = ap.parse_args()

    files = [p for p in sorted(args.memory_dir.glob("*.md"))
             if p.name != "MEMORY.md" and not p.name.startswith("._")]
    names = set()
    for p in files:
        m = _parse_file(p)
        if m is not None:
            names.add(m.name)

    total = 0
    for p in files:
        text = p.read_text(encoding="utf-8")
        missing = []

        def relink(match):
            if match.group(1) in names:
                return match.group(0)
            missing.append(match.group(1))
            return f"`{match.group(1)}`"

        fixed = WIKILINK_RE.sub(relink, text)
        if missing:
            total += len(missing)
            print(f"{p.name}: {len(missing)}")
            if not args.dry_run:
                p.write_text(fixed, encoding="utf-8")
    print(f"{total} dangling links {'found' if args.dry_run else 'relinked'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
