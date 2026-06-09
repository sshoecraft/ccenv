#!/usr/bin/env python3
"""
Mermaid Call Graph Generator for Claude Code Project Awareness System

Generates Mermaid call graph diagrams from a structural map file or by
analyzing a project directory directly.

Usage:
    python3 generate_mermaid_callgraph.py <structural-map.md>
    python3 generate_mermaid_callgraph.py --project <project_dir>
    python3 generate_mermaid_callgraph.py <structural-map.md> --subsystem cache
    python3 generate_mermaid_callgraph.py <structural-map.md> --cross-subsystem-only
"""

import os
import sys
import re
from collections import defaultdict

VERSION = "1.0.0"


def mermaid_id(name):
    """Sanitize a name for use as a Mermaid node ID."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


# ═══════════════════════════════════════════════════════════════════
# Parse structural map markdown
# ═══════════════════════════════════════════════════════════════════

def parse_structural_map(content):
    """Parse a structural-map.md file and extract functions and call edges.

    Returns:
        files: dict of filepath -> {functions: [...], types: [...]}
        All function data includes 'calls' and 'called_by' lists.
    """
    files = {}
    current_file = None
    current_func = None
    in_map = False

    for line in content.split('\n'):
        stripped = line.strip()

        # Detect start of map section
        if stripped == '## Map':
            in_map = True
            continue
        if in_map and stripped.startswith('## '):
            in_map = False
            continue
        if not in_map:
            continue

        # Skip code block markers
        if stripped == '```' or stripped == '```text':
            continue

        # File header: [path/to/file.ext]
        m = re.match(r'^\[(.+)\]$', stripped)
        if m:
            current_file = m.group(1)
            files[current_file] = {'functions': [], 'types': []}
            current_func = None
            continue

        if not current_file:
            continue

        # Function: fn name(params) -> ret
        m = re.match(r'^\s*fn\s+(?:static\s+)?(\w+)\s*\(([^)]*)\)(?:\s*->\s*(.+))?$', stripped)
        if m:
            current_func = {
                'name': m.group(1),
                'params': m.group(2).strip(),
                'return_type': m.group(3).strip() if m.group(3) else None,
                'calls': [],
                'called_by': [],
                'is_static': 'static' in stripped.split(m.group(1))[0],
            }
            files[current_file]['functions'].append(current_func)
            continue

        # calls: line
        if current_func and stripped.startswith('calls:'):
            callees = stripped[len('calls:'):].strip()
            current_func['calls'] = [c.strip() for c in callees.split(',') if c.strip()]
            continue

        # called_by: line
        if current_func and stripped.startswith('called_by:'):
            callers = stripped[len('called_by:'):].strip()
            current_func['called_by'] = [c.strip() for c in callers.split(',') if c.strip()]
            continue

        # struct/enum/union
        m = re.match(r'^\s*(struct|enum|union)\s+(\w+)', stripped)
        if m:
            current_func = None
            files[current_file]['types'].append({
                'kind': m.group(1),
                'name': m.group(2),
            })
            continue

    return files


def detect_subsystem_from_path(filepath):
    """Determine the subsystem from the file path."""
    parts = filepath.replace('\\', '/').split('/')
    if len(parts) <= 1:
        return 'root'
    return parts[0]


# ═══════════════════════════════════════════════════════════════════
# Mermaid generation
# ═══════════════════════════════════════════════════════════════════

def generate_full_callgraph(files, subsystem_filter=None):
    """Generate a full Mermaid call graph, optionally filtered to one subsystem."""
    lines = ['graph TD']

    # Build function → subsystem mapping
    func_to_sub = {}
    all_funcs = set()
    for fpath, data in files.items():
        sub = detect_subsystem_from_path(fpath)
        for func in data['functions']:
            func_to_sub[func['name']] = sub
            all_funcs.add(func['name'])

    # Collect edges
    edges = set()
    for fpath, data in files.items():
        for func in data['functions']:
            for callee in func.get('calls', []):
                if callee in all_funcs:
                    edges.add((func['name'], callee))

    if not edges:
        lines.append('    empty[No call graph edges found]')
        return '\n'.join(lines)

    # Filter if requested
    if subsystem_filter:
        filtered_edges = set()
        for src, dst in edges:
            src_sub = func_to_sub.get(src, '')
            dst_sub = func_to_sub.get(dst, '')
            if subsystem_filter in (src_sub, dst_sub):
                filtered_edges.add((src, dst))
        edges = filtered_edges

    if not edges:
        lines.append('    empty[No edges matching filter]')
        return '\n'.join(lines)

    # Determine active subsystems and functions
    active_funcs = set()
    for src, dst in edges:
        active_funcs.add(src)
        active_funcs.add(dst)

    active_subs = defaultdict(set)
    for fname in active_funcs:
        sub = func_to_sub.get(fname, 'other')
        active_subs[sub].add(fname)

    # Write subgraphs
    for sub in sorted(active_subs.keys()):
        sub_label = mermaid_id(sub)
        lines.append('    subgraph {}[{}]'.format(sub_label, sub))
        for fname in sorted(active_subs[sub]):
            lines.append('        {}[{}]'.format(mermaid_id(fname), fname))
        lines.append('    end')

    # Write edges
    for src, dst in sorted(edges):
        lines.append('    {} --> {}'.format(mermaid_id(src), mermaid_id(dst)))

    return '\n'.join(lines)


def generate_cross_subsystem_callgraph(files):
    """Generate Mermaid diagram showing ONLY cross-subsystem calls.
    These are the most architecturally important edges.
    """
    lines = ['graph TD']
    lines.append('    %% Cross-subsystem calls only — the most architecturally sensitive paths')

    func_to_sub = {}
    all_funcs = set()
    for fpath, data in files.items():
        sub = detect_subsystem_from_path(fpath)
        for func in data['functions']:
            func_to_sub[func['name']] = sub
            all_funcs.add(func['name'])

    # Find cross-subsystem edges
    cross_edges = set()
    for fpath, data in files.items():
        for func in data['functions']:
            src_sub = func_to_sub.get(func['name'], 'other')
            for callee in func.get('calls', []):
                if callee in func_to_sub:
                    dst_sub = func_to_sub[callee]
                    if src_sub != dst_sub:
                        cross_edges.add((func['name'], callee, src_sub, dst_sub))

    if not cross_edges:
        lines.append('    none[No cross-subsystem calls detected]')
        return '\n'.join(lines)

    # Group functions by subsystem
    sub_funcs = defaultdict(set)
    for src, dst, ssub, dsub in cross_edges:
        sub_funcs[ssub].add(src)
        sub_funcs[dsub].add(dst)

    # Write subgraphs
    for sub in sorted(sub_funcs.keys()):
        sub_label = mermaid_id(sub)
        lines.append('    subgraph {}[{}]'.format(sub_label, sub))
        for fname in sorted(sub_funcs[sub]):
            lines.append('        {}[{}]'.format(mermaid_id(fname), fname))
        lines.append('    end')

    # Write cross-subsystem edges
    rendered = set()
    for src, dst, ssub, dsub in sorted(cross_edges):
        key = (src, dst)
        if key not in rendered:
            rendered.add(key)
            lines.append('    {} --> {}'.format(mermaid_id(src), mermaid_id(dst)))

    # Summary comment
    lines.append('')
    lines.append('    %% {} cross-subsystem edges between {} subsystems'.format(
        len(rendered), len(sub_funcs)))

    return '\n'.join(lines)


def generate_subsystem_summary(files):
    """Generate a high-level subsystem interaction diagram."""
    lines = ['graph LR']
    lines.append('    %% Subsystem-level interaction summary')

    func_to_sub = {}
    for fpath, data in files.items():
        sub = detect_subsystem_from_path(fpath)
        for func in data['functions']:
            func_to_sub[func['name']] = sub

    # Find subsystem-to-subsystem edges
    sub_edges = defaultdict(int)
    for fpath, data in files.items():
        for func in data['functions']:
            src_sub = func_to_sub.get(func['name'], 'other')
            for callee in func.get('calls', []):
                if callee in func_to_sub:
                    dst_sub = func_to_sub[callee]
                    if src_sub != dst_sub:
                        sub_edges[(src_sub, dst_sub)] += 1

    if not sub_edges:
        lines.append('    none[No inter-subsystem dependencies]')
        return '\n'.join(lines)

    # Count functions per subsystem
    sub_func_count = defaultdict(int)
    for fpath, data in files.items():
        sub = detect_subsystem_from_path(fpath)
        sub_func_count[sub] += len(data['functions'])

    # All participating subsystems
    all_subs = set()
    for (s, d), count in sub_edges.items():
        all_subs.add(s)
        all_subs.add(d)

    # Nodes
    for sub in sorted(all_subs):
        count = sub_func_count.get(sub, 0)
        lines.append('    {}["{} ({} fns)"]'.format(mermaid_id(sub), sub, count))

    # Edges with weights
    for (src, dst), count in sorted(sub_edges.items()):
        label = '{} calls'.format(count) if count > 1 else '1 call'
        lines.append('    {} -->|{}| {}'.format(mermaid_id(src), label, mermaid_id(dst)))

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Generate Mermaid call graph from structural map or project directory'
    )
    parser.add_argument('input', nargs='?',
                        help='Structural map file (.md) to parse')
    parser.add_argument('--project', '-p', metavar='DIR',
                        help='Project directory (runs generate_structural_map internally)')
    parser.add_argument('--subsystem', '-s', metavar='NAME',
                        help='Filter to show only calls involving this subsystem')
    parser.add_argument('--cross-subsystem-only', '-x', action='store_true',
                        help='Show only cross-subsystem calls')
    parser.add_argument('--summary', action='store_true',
                        help='Show subsystem-level summary diagram')
    parser.add_argument('--output', '-o', metavar='FILE',
                        help='Output file (default: stdout)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print progress to stderr')
    parser.add_argument('--version', action='version',
                        version='%(prog)s ' + VERSION)
    args = parser.parse_args()

    if not args.input and not args.project:
        parser.error('Provide a structural map file or --project <dir>')

    files = None

    if args.input:
        # Parse structural map file
        if not os.path.isfile(args.input):
            print("Error: {} not found".format(args.input), file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print("Parsing structural map: {}".format(args.input), file=sys.stderr)

        with open(args.input, 'r', errors='ignore') as f:
            content = f.read()
        files = parse_structural_map(content)

    elif args.project:
        # Run generate_structural_map.py on the project and parse its output
        project_dir = os.path.abspath(args.project)
        if not os.path.isdir(project_dir):
            print("Error: {} is not a directory".format(project_dir), file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print("Analyzing project: {}".format(project_dir), file=sys.stderr)

        # Import the structural map generator
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, script_dir)
        try:
            import generate_structural_map as gsm
        except ImportError:
            print("Error: generate_structural_map.py not found in {}".format(script_dir),
                  file=sys.stderr)
            sys.exit(1)

        # Scan and parse files
        file_data = {}
        all_functions = set()

        for root, dirs, filenames in os.walk(project_dir):
            dirs[:] = sorted([d for d in dirs if not gsm.should_skip(d)])
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in gsm.ALL_EXTENSIONS:
                    continue
                filepath = os.path.join(root, name)
                rel_path = os.path.relpath(filepath, project_dir)
                content = gsm.read_file(filepath)
                if not content:
                    continue
                functions, types = gsm.parse_file(filepath, content, ext)
                file_data[rel_path] = {'functions': functions, 'types': types}
                for func in functions:
                    all_functions.add(func['name'])

        files = file_data

    if not files:
        print("Error: no data to process", file=sys.stderr)
        sys.exit(1)

    # Count stats
    total_funcs = sum(len(d['functions']) for d in files.values())
    total_edges = sum(
        len(f.get('calls', []))
        for d in files.values()
        for f in d['functions']
    )
    if args.verbose:
        print("Loaded {} files, {} functions, {} call edges".format(
            len(files), total_funcs, total_edges), file=sys.stderr)

    # Generate the requested diagram
    if args.summary:
        output = generate_subsystem_summary(files)
    elif args.cross_subsystem_only:
        output = generate_cross_subsystem_callgraph(files)
    elif args.subsystem:
        output = generate_full_callgraph(files, subsystem_filter=args.subsystem)
    else:
        output = generate_full_callgraph(files)

    # Output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(output)
            f.write('\n')
        print("Mermaid diagram written to {}".format(args.output), file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
