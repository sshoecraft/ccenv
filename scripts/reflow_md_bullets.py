#!/usr/bin/env python3
"""Reflow markdown list items to a fixed width with a hanging indent.

Editing rule files by search-and-replace leaves bullets wrapped at whatever
column the old text happened to end on. This rejoins each list item and rewraps
it, leaving headings, blank lines and ordinary paragraphs untouched.

    python3 scripts/reflow_md_bullets.py CLAUDE.md.draft [--width 100]
"""
import argparse
import re
import textwrap

BULLET = re.compile(r'^(\s*)([-*]|\d+\.)\s+(.*)$')


def reflow(text, width):
    out = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        m = BULLET.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent, marker, first = m.groups()
        body = [first]
        i += 1
        # Continuation lines are indented further than the marker and are not
        # themselves list items or blank.
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip() or BULLET.match(nxt):
                break
            if not nxt.startswith(indent + ' '):
                break
            body.append(nxt.strip())
            i += 1
        joined = ' '.join(part for part in body if part)
        lead = f'{indent}{marker} '
        hang = ' ' * len(lead)
        out.extend(textwrap.wrap(joined, width=width,
                                 initial_indent=lead, subsequent_indent=hang,
                                 break_long_words=False, break_on_hyphens=False))
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--width', type=int, default=100)
    args = ap.parse_args()
    with open(args.path) as fh:
        text = fh.read()
    with open(args.path, 'w') as fh:
        fh.write(reflow(text, args.width))


if __name__ == '__main__':
    main()
