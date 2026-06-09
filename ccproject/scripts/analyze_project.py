#!/usr/bin/env python3
"""
Project Analyzer for Claude Code Project Awareness System

Analyzes a project directory to auto-detect languages, identify subsystems,
and map inter-subsystem dependencies. Outputs a JSON report used during
the bootstrap phase.

Usage:
    python3 analyze_project.py <project_dir> [--verbose]
"""

import os
import sys
import json
import re
from collections import defaultdict

VERSION = "1.0.0"

# ── File extension → language mapping ──

LANG_EXTENSIONS = {
    '.c': 'C', '.h': 'C/C++ Header',
    '.cpp': 'C++', '.cxx': 'C++', '.cc': 'C++',
    '.hpp': 'C++ Header', '.hxx': 'C++ Header', '.hh': 'C++ Header',
    '.py': 'Python',
    '.js': 'JavaScript', '.jsx': 'JavaScript',
    '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.go': 'Go',
    '.rs': 'Rust',
    '.java': 'Java',
    '.rb': 'Ruby',
    '.swift': 'Swift',
    '.kt': 'Kotlin',
    '.vue': 'Vue',
    '.sh': 'Shell', '.bash': 'Shell', '.zsh': 'Shell',
    '.pl': 'Perl', '.pm': 'Perl',
    '.lua': 'Lua',
    '.zig': 'Zig',
}

# Aggregate language families (headers count toward parent)
LANG_FAMILY = {
    'C/C++ Header': 'C',
    'C++ Header': 'C++',
}

# ── Directories to skip ──

SKIP_DIRS = {
    '.git', '.svn', '.hg',
    'node_modules', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.build', 'target', 'vendor',
    '.cache', '.tox', '.eggs', '.mypy_cache', '.pytest_cache',
    'coverage', '.coverage', '.next', '.nuxt',
    '.idea', '.vscode',
}

# ── Build system detection ──

BUILD_SYSTEMS = {
    'Makefile': 'Make',
    'GNUmakefile': 'Make',
    'makefile': 'Make',
    'CMakeLists.txt': 'CMake',
    'configure.ac': 'Autotools',
    'configure.in': 'Autotools',
    'meson.build': 'Meson',
    'BUILD': 'Bazel',
    'BUILD.bazel': 'Bazel',
    'WORKSPACE': 'Bazel',
    'package.json': 'npm',
    'Cargo.toml': 'Cargo',
    'go.mod': 'Go Modules',
    'setup.py': 'setuptools',
    'pyproject.toml': 'pyproject',
    'Gemfile': 'Bundler',
    'build.gradle': 'Gradle',
    'pom.xml': 'Maven',
    'Kbuild': 'Kbuild',
    'SConstruct': 'SCons',
    'Tupfile': 'Tup',
    'Justfile': 'Just',
}


def should_skip(dirname):
    """Check if a directory should be skipped during scanning."""
    if dirname.startswith('.'):
        return True
    return dirname in SKIP_DIRS


def count_lines(filepath):
    """Count lines in a file, handling encoding errors gracefully."""
    try:
        with open(filepath, 'r', errors='ignore') as f:
            return sum(1 for _ in f)
    except (IOError, OSError):
        return 0


def scan_files(project_dir):
    """Walk the project tree and collect source file info."""
    files = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = sorted([d for d in dirs if not should_skip(d)])
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in LANG_EXTENSIONS:
                continue
            filepath = os.path.join(root, name)
            rel_path = os.path.relpath(filepath, project_dir)
            lines = count_lines(filepath)
            files.append({
                'path': rel_path,
                'name': name,
                'ext': ext,
                'language': LANG_EXTENSIONS[ext],
                'lines': lines,
            })
    return files


def detect_build_systems(project_dir):
    """Detect build system(s) present in the project root and subdirs."""
    found = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not should_skip(d)]
        depth = root[len(project_dir):].count(os.sep)
        if depth > 2:
            continue
        for name in filenames:
            if name in BUILD_SYSTEMS:
                system = BUILD_SYSTEMS[name]
                rel = os.path.relpath(os.path.join(root, name), project_dir)
                entry = {'system': system, 'file': rel}
                if entry not in found:
                    found.append(entry)
    # Deduplicate by system name, keep first occurrence
    seen = set()
    unique = []
    for e in found:
        if e['system'] not in seen:
            seen.add(e['system'])
            unique.append(e)
    return unique


def identify_subsystems(project_dir, files):
    """Identify subsystems from directory structure and file organization.

    Groups files by their top-level directory. For nested source trees
    (e.g., src/cache/, src/dlm/), promotes the second level if 'src' or
    'lib' is the sole top-level directory containing most code.
    """
    dir_groups = defaultdict(list)
    top_level_files = []

    for f in files:
        parts = f['path'].split(os.sep)
        if len(parts) == 1:
            top_level_files.append(f)
        else:
            dir_groups[parts[0]].append(f)

    # Check if we should promote a level (e.g., src/ with subdirs)
    promoted = {}
    for dirname, dir_files in list(dir_groups.items()):
        if dirname.lower() in ('src', 'lib', 'libmxfs', 'pkg', 'internal', 'crates'):
            sub_groups = defaultdict(list)
            direct_files = []
            for f in dir_files:
                parts = f['path'].split(os.sep)
                if len(parts) <= 2:
                    direct_files.append(f)
                else:
                    sub_groups[parts[1]].append(f)

            if len(sub_groups) >= 2:
                # Promote subdirectories as subsystems
                for subdir, sub_files in sub_groups.items():
                    promoted[dirname + '/' + subdir] = sub_files
                if direct_files:
                    promoted[dirname] = direct_files
                del dir_groups[dirname]

    dir_groups.update(promoted)

    subsystems = []
    for dirname in sorted(dir_groups.keys()):
        dir_files = dir_groups[dirname]
        lang_counts = defaultdict(int)
        total_lines = 0
        for f in dir_files:
            lang = LANG_FAMILY.get(f['language'], f['language'])
            lang_counts[lang] += 1
            total_lines += f['lines']

        if total_lines == 0:
            continue

        primary_lang = max(lang_counts, key=lang_counts.get)
        purpose = guess_purpose(dirname, dir_files, project_dir)
        public_api = extract_public_api_names(project_dir, dir_files)

        subsystems.append({
            'name': dirname.replace('/', '_').replace('\\', '_'),
            'directory': dirname,
            'file_count': len(dir_files),
            'line_count': total_lines,
            'primary_language': primary_lang,
            'languages': dict(lang_counts),
            'purpose': purpose,
            'public_api_functions': public_api[:30],
            'files': sorted([f['path'] for f in dir_files]),
        })

    # Top-level files
    if top_level_files:
        lang_counts = defaultdict(int)
        total_lines = 0
        for f in top_level_files:
            lang = LANG_FAMILY.get(f['language'], f['language'])
            lang_counts[lang] += 1
            total_lines += f['lines']
        if total_lines > 0:
            subsystems.append({
                'name': 'root',
                'directory': '.',
                'file_count': len(top_level_files),
                'line_count': total_lines,
                'primary_language': max(lang_counts, key=lang_counts.get),
                'languages': dict(lang_counts),
                'purpose': 'Top-level source files',
                'public_api_functions': [],
                'files': sorted([f['path'] for f in top_level_files]),
            })

    return subsystems


def guess_purpose(dirname, files, project_dir):
    """Guess the purpose of a subsystem from its name and contents."""
    basename = os.path.basename(dirname).lower()

    known = {
        'src': 'Main source code',
        'lib': 'Core library',
        'libmxfs': 'Core filesystem library',
        'include': 'Public header files',
        'test': 'Test suite', 'tests': 'Test suite',
        'doc': 'Documentation', 'docs': 'Documentation',
        'tools': 'Command-line tools and utilities',
        'scripts': 'Build and utility scripts',
        'api': 'API layer',
        'frontend': 'Frontend / VFS integration layer',
        'backend': 'Backend application',
        'config': 'Configuration',
        'pal': 'Platform abstraction layer',
        'packaging': 'Package build files',
        'core': 'Core subsystem',
        'utils': 'Utility functions',
        'common': 'Common/shared code',
        'cmd': 'Command-line entry points',
        'bin': 'Binary entry points',
        'examples': 'Example code',
    }

    if basename in known:
        return known[basename]

    # Check for README or similar in the directory
    dir_path = os.path.join(project_dir, dirname)
    for readme_name in ('README.md', 'README', 'README.txt'):
        readme_path = os.path.join(dir_path, readme_name)
        if os.path.isfile(readme_path):
            try:
                with open(readme_path, 'r', errors='ignore') as f:
                    first_line = ''
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            first_line = line
                            break
                        elif line.startswith('#'):
                            first_line = line.lstrip('#').strip()
                            break
                    if first_line:
                        return first_line[:80]
            except (IOError, OSError):
                pass

    # Infer from file types present
    has_headers = any(f['ext'] in ('.h', '.hpp', '.hxx') for f in files)
    has_c_sources = any(f['ext'] in ('.c', '.cpp', '.cxx', '.cc') for f in files)
    has_python = any(f['ext'] == '.py' for f in files)

    if has_headers and has_c_sources:
        return 'C/C++ library module'
    elif has_headers:
        return 'Header-only module'
    elif has_python:
        return 'Python module'

    return 'Module'


def extract_public_api_names(project_dir, files):
    """Extract public function names from header files (C/C++) or module files."""
    api_names = []

    for f in files:
        if f['ext'] not in ('.h', '.hpp', '.hxx'):
            continue

        filepath = os.path.join(project_dir, f['path'])
        try:
            with open(filepath, 'r', errors='ignore') as fh:
                content = fh.read()
        except (IOError, OSError):
            continue

        # Join continuation lines: if a line ends with , or ( and next is indented
        lines = content.split('\n')
        joined = []
        buf = ''
        for line in lines:
            stripped = line.strip()
            if buf:
                buf += ' ' + stripped
                if ');' in stripped or (stripped.endswith(')') and not stripped.endswith('),')):
                    joined.append(buf)
                    buf = ''
            elif stripped and not stripped.startswith('#') and not stripped.startswith('/*') \
                    and not stripped.startswith('*') and not stripped.startswith('//'):
                if '(' in stripped and ');' not in stripped and '{' not in stripped:
                    # Possible multi-line declaration
                    buf = stripped
                else:
                    joined.append(stripped)
            else:
                joined.append(stripped)
        if buf:
            joined.append(buf)

        for line in joined:
            # Match function declarations
            m = re.match(
                r'(?:extern\s+)?'
                r'(?:const\s+)?(?:unsigned\s+)?(?:struct\s+)?'
                r'[\w][\w\s\*]*?'
                r'\s+\*?(\w+)\s*\([^)]*\)\s*;',
                line
            )
            if m:
                name = m.group(1)
                keywords = {'if', 'while', 'for', 'switch', 'return', 'sizeof',
                            'typeof', 'alignof', 'defined'}
                if name not in keywords and not name.startswith('_'):
                    api_names.append(name)

    return api_names


def detect_dependencies(project_dir, subsystems, files):
    """Detect inter-subsystem dependencies from include/import statements."""
    # Build lookup: directory → subsystem name
    dir_to_subsystem = {}
    for s in subsystems:
        dir_to_subsystem[s['directory']] = s['name']
        # Also map individual files
        for fpath in s['files']:
            dir_to_subsystem[fpath] = s['name']

    def file_to_subsystem(fpath):
        parts = fpath.split(os.sep)
        if len(parts) == 1:
            return 'root'
        # Try longest prefix match
        for i in range(len(parts) - 1, 0, -1):
            prefix = os.sep.join(parts[:i])
            if prefix in dir_to_subsystem:
                return dir_to_subsystem[prefix]
        return parts[0]

    edges = set()

    for f in files:
        filepath = os.path.join(project_dir, f['path'])
        from_sub = file_to_subsystem(f['path'])

        try:
            with open(filepath, 'r', errors='ignore') as fh:
                content = fh.read()
        except (IOError, OSError):
            continue

        lang = f['language']

        if lang in ('C', 'C/C++ Header', 'C++', 'C++ Header'):
            for m in re.finditer(r'#include\s*"([^"]+)"', content):
                inc_path = m.group(1)
                # Resolve relative to file directory
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(f['path']), inc_path)
                )
                to_sub = file_to_subsystem(resolved)
                if to_sub and to_sub != from_sub:
                    edges.add((from_sub, to_sub))

        elif lang == 'Python':
            for m in re.finditer(r'^(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
                mod = m.group(1).split('.')[0]
                if mod in dir_to_subsystem:
                    to_sub = dir_to_subsystem[mod]
                    if to_sub != from_sub:
                        edges.add((from_sub, to_sub))

        elif lang in ('JavaScript', 'TypeScript'):
            for m in re.finditer(r"(?:import|require)\s*\(?['\"]\.\.?/([^'\"]+)", content):
                imp_path = m.group(1).split('/')[0]
                if imp_path in dir_to_subsystem:
                    to_sub = dir_to_subsystem[imp_path]
                    if to_sub != from_sub:
                        edges.add((from_sub, to_sub))

        elif lang == 'Go':
            for m in re.finditer(r'"([^"]+)"', content):
                parts = m.group(1).split('/')
                pkg = parts[-1] if parts else ''
                if pkg in dir_to_subsystem:
                    to_sub = dir_to_subsystem[pkg]
                    if to_sub != from_sub:
                        edges.add((from_sub, to_sub))

        elif lang == 'Rust':
            for m in re.finditer(r'(?:use|mod)\s+(\w+)', content):
                mod = m.group(1)
                if mod in dir_to_subsystem:
                    to_sub = dir_to_subsystem[mod]
                    if to_sub != from_sub:
                        edges.add((from_sub, to_sub))

    return [{'from': e[0], 'to': e[1]} for e in sorted(edges)]


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Analyze project structure for Claude Code awareness bootstrap'
    )
    parser.add_argument('project_dir', help='Project directory to analyze')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Print progress to stderr')
    parser.add_argument('--version', action='version',
                        version='%(prog)s ' + VERSION)
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    if not os.path.isdir(project_dir):
        print("Error: {} is not a directory".format(project_dir), file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print("Scanning {}...".format(project_dir), file=sys.stderr)

    # Scan all source files
    files = scan_files(project_dir)
    if args.verbose:
        print("Found {} source files".format(len(files)), file=sys.stderr)

    # Aggregate language statistics
    lang_stats = defaultdict(lambda: {'files': 0, 'lines': 0})
    total_lines = 0
    for f in files:
        lang = LANG_FAMILY.get(f['language'], f['language'])
        lang_stats[lang]['files'] += 1
        lang_stats[lang]['lines'] += f['lines']
        total_lines += f['lines']

    # Detect build systems
    build_systems = detect_build_systems(project_dir)

    # Identify subsystems
    subsystems = identify_subsystems(project_dir, files)
    if args.verbose:
        print("Identified {} subsystems".format(len(subsystems)), file=sys.stderr)

    # Detect dependencies
    dependencies = detect_dependencies(project_dir, subsystems, files)
    if args.verbose:
        print("Found {} inter-subsystem dependencies".format(len(dependencies)),
              file=sys.stderr)

    # Build report
    report = {
        'version': VERSION,
        'project': {
            'name': os.path.basename(project_dir),
            'path': project_dir,
            'total_source_files': len(files),
            'total_lines': total_lines,
            'languages': {k: dict(v) for k, v in sorted(
                lang_stats.items(), key=lambda x: x[1]['lines'], reverse=True
            )},
            'build_systems': [bs['system'] for bs in build_systems],
            'build_files': build_systems,
        },
        'subsystems': subsystems,
        'dependencies': dependencies,
    }

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
