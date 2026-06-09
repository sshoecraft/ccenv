#!/usr/bin/env python3
"""
Structural Map Generator for Claude Code Project Awareness System

Parses source files to extract function signatures, type definitions,
and call graph edges. Outputs in compact notation format for the
structural-map.md and optionally generates Mermaid diagrams.

Supports tree-sitter for accurate AST parsing when available,
falls back to regex-based extraction (zero dependencies).

Usage:
    python3 generate_structural_map.py <project_dir> [--mermaid] [--output DIR]
"""

import os
import sys
import re
import json
from collections import defaultdict, OrderedDict

VERSION = "1.0.0"

# ── Try to load tree-sitter ──

HAS_TREE_SITTER = False
HAS_TS_C = False
HAS_TS_CPP = False
HAS_TS_PYTHON = False
HAS_TS_JS = False
HAS_TS_GO = False
HAS_TS_RUST = False

try:
    from tree_sitter import Language, Parser
    HAS_TREE_SITTER = True

    try:
        import tree_sitter_c as _tsc
        HAS_TS_C = True
    except ImportError:
        pass
    try:
        import tree_sitter_cpp as _tscpp
        HAS_TS_CPP = True
    except ImportError:
        pass
    try:
        import tree_sitter_python as _tspy
        HAS_TS_PYTHON = True
    except ImportError:
        pass
    try:
        import tree_sitter_javascript as _tsjs
        HAS_TS_JS = True
    except ImportError:
        pass
    try:
        import tree_sitter_go as _tsgo
        HAS_TS_GO = True
    except ImportError:
        pass
    try:
        import tree_sitter_rust as _tsrs
        HAS_TS_RUST = True
    except ImportError:
        pass
except ImportError:
    pass

# ── Constants ──

SKIP_DIRS = {
    '.git', '.svn', '.hg', 'node_modules', '__pycache__', '.venv', 'venv',
    'env', 'dist', 'build', '.build', 'target', 'vendor', '.cache',
    '.tox', '.eggs', '.mypy_cache', '.pytest_cache', '.next', '.nuxt',
    '.idea', '.vscode',
}

C_KEYWORDS = {
    'if', 'else', 'while', 'for', 'do', 'switch', 'case', 'default',
    'return', 'break', 'continue', 'goto', 'sizeof', 'typeof', 'alignof',
    'defined', 'offsetof', 'static_assert', 'NULL', 'true', 'false',
}

C_EXTENSIONS = {'.c', '.h'}
CPP_EXTENSIONS = {'.cpp', '.cxx', '.cc', '.hpp', '.hxx', '.hh'}
PY_EXTENSIONS = {'.py'}
JS_EXTENSIONS = {'.js', '.jsx', '.ts', '.tsx'}
GO_EXTENSIONS = {'.go'}
RUST_EXTENSIONS = {'.rs'}

ALL_EXTENSIONS = C_EXTENSIONS | CPP_EXTENSIONS | PY_EXTENSIONS | JS_EXTENSIONS | GO_EXTENSIONS | RUST_EXTENSIONS


def should_skip(dirname):
    if dirname.startswith('.'):
        return True
    return dirname in SKIP_DIRS


def read_file(filepath):
    try:
        with open(filepath, 'r', errors='ignore') as f:
            return f.read()
    except (IOError, OSError):
        return ''


def estimate_tokens(text):
    """Rough token count estimation (words + punctuation)."""
    return len(text.split()) + text.count('(') + text.count(')') + text.count('{') + text.count('}')


# ═══════════════════════════════════════════════════════════════════
# Regex-based parsers (zero dependency fallback)
# ═══════════════════════════════════════════════════════════════════

def join_continuation_lines(content):
    """Join lines that are continuations of function declarations/definitions."""
    lines = content.split('\n')
    result = []
    buf = ''
    paren_depth = 0

    for line in lines:
        stripped = line.strip()

        if buf:
            buf += ' ' + stripped
            paren_depth += stripped.count('(') - stripped.count(')')
            if paren_depth <= 0:
                result.append(buf)
                buf = ''
                paren_depth = 0
        else:
            # Check if this line starts a multi-line declaration/definition
            if (stripped and not stripped.startswith('#') and
                    not stripped.startswith('/*') and not stripped.startswith('*') and
                    not stripped.startswith('//') and not stripped.startswith('}')):
                paren_depth = stripped.count('(') - stripped.count(')')
                if paren_depth > 0 and '(' in stripped:
                    buf = stripped
                else:
                    result.append(line)
                    paren_depth = 0
            else:
                result.append(line)

    if buf:
        result.append(buf)
    return result


def regex_parse_c_header(filepath, content):
    """Parse a C/C++ header file using regex. Returns functions, types, macros."""
    functions = []
    types = []
    macros = []

    joined = join_continuation_lines(content)

    for line in joined:
        stripped = line.strip() if isinstance(line, str) else line

        # Skip preprocessor, comments
        if not stripped or stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*'):
            # But catch function-like macros
            m = re.match(r'#define\s+(\w+)\s*\(([^)]*)\)', stripped)
            if m:
                macros.append({
                    'name': m.group(1),
                    'params': m.group(2).strip(),
                })
            continue

        # Typedef function pointer: typedef ret (*name)(params);
        m = re.match(
            r'typedef\s+[\w\s\*]+\s*\(\s*\*\s*(\w+)\s*\)\s*\([^)]*\)\s*;',
            stripped
        )
        if m:
            types.append({
                'kind': 'typedef_fn',
                'name': m.group(1),
                'definition': stripped.rstrip(';').strip(),
            })
            continue

        # Struct/union/enum definition
        m = re.match(
            r'(?:typedef\s+)?(struct|union|enum)\s+(\w+)\s*\{',
            stripped
        )
        if m:
            kind = m.group(1)
            name = m.group(2)
            # Extract fields from the original content
            fields = extract_struct_fields(content, name, kind)
            types.append({
                'kind': kind,
                'name': name,
                'fields': fields,
            })
            continue

        # Function declaration: type name(params);
        m = re.match(
            r'(?:extern\s+)?(?:static\s+inline\s+|static\s+|inline\s+)?'
            r'((?:const\s+)?(?:unsigned\s+)?(?:struct\s+)?[\w][\w\s]*?[\w\*])'
            r'\s+\*?(\w+)\s*\(([^)]*)\)\s*;',
            stripped
        )
        if m:
            ret_type = m.group(1).strip()
            name = m.group(2)
            params = m.group(3).strip()
            if name not in C_KEYWORDS:
                functions.append({
                    'name': name,
                    'return_type': ret_type,
                    'params': simplify_params(params),
                    'is_declaration': True,
                    'is_static': 'static' in stripped.split(name)[0],
                })
            continue

    return functions, types, macros


def regex_parse_c_source(filepath, content):
    """Parse a C/C++ source file using regex. Returns functions with call edges."""
    functions = []
    types = []

    # Find function definitions by looking for the pattern:
    # type name(params) {
    # We need to handle multi-line signatures
    joined = join_continuation_lines(content)

    # Also need to find function bodies for call graph
    # Strategy: find function starts, then track brace depth for body

    # First pass: find all function definitions
    func_pattern = re.compile(
        r'(?:static\s+inline\s+|static\s+|inline\s+)?'
        r'((?:const\s+)?(?:unsigned\s+)?(?:struct\s+)?[\w][\w\s]*?[\w\*])'
        r'\s+\*?(\w+)\s*\(([^)]*)\)\s*$'
    )

    for line in joined:
        stripped = line.strip() if isinstance(line, str) else line
        if not stripped or stripped.startswith('#') or stripped.startswith('//'):
            continue

        # Check for function definition (line ending with ) or )\n{)
        clean = stripped.rstrip('{').rstrip()
        m = func_pattern.match(clean)
        if m:
            ret_type = m.group(1).strip()
            name = m.group(2)
            params = m.group(3).strip()
            if name not in C_KEYWORDS:
                is_static = 'static' in stripped.split(name)[0]
                functions.append({
                    'name': name,
                    'return_type': ret_type,
                    'params': simplify_params(params),
                    'is_declaration': False,
                    'is_static': is_static,
                    'calls': [],
                })

        # Struct definitions in .c files
        m = re.match(r'(?:typedef\s+)?(struct|union|enum)\s+(\w+)\s*\{', stripped)
        if m:
            types.append({
                'kind': m.group(1),
                'name': m.group(2),
                'fields': extract_struct_fields(content, m.group(2), m.group(1)),
            })

    # Second pass: extract call edges from function bodies
    extract_calls_from_source(content, functions)

    return functions, types


def extract_calls_from_source(content, functions):
    """Extract function calls from C source by finding function bodies."""
    lines = content.split('\n')
    func_names = {f['name'] for f in functions}

    # Build a map of function name → line number for definitions
    func_starts = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        for f in functions:
            if f['name'] + '(' in stripped and not stripped.startswith('#'):
                # Check this looks like a definition (has return type before name)
                before = stripped.split(f['name'])[0].strip()
                if before and not before.endswith('=') and not before.endswith(','):
                    func_starts.append((i, f))
                    break

    # For each function, find its body and extract calls
    for idx, (start_line, func) in enumerate(func_starts):
        # Find the opening brace
        brace_start = -1
        for i in range(start_line, min(start_line + 5, len(lines))):
            if '{' in lines[i]:
                brace_start = i
                break

        if brace_start < 0:
            continue

        # Track brace depth to find end of function
        depth = 0
        body_lines = []
        for i in range(brace_start, len(lines)):
            line = lines[i]
            depth += line.count('{') - line.count('}')
            body_lines.append(line)
            if depth <= 0:
                break

        # Extract calls from body
        body = '\n'.join(body_lines)
        calls = set()
        for m in re.finditer(r'\b(\w+)\s*\(', body):
            callee = m.group(1)
            if callee not in C_KEYWORDS and callee != func['name']:
                # Filter out common macros and type casts
                if not callee.isupper() or len(callee) <= 3:
                    if not callee.isupper():
                        calls.add(callee)

        func['calls'] = sorted(calls)


def extract_struct_fields(content, name, kind):
    """Extract field names from a struct/union/enum definition."""
    fields = []
    # Find the struct definition
    pattern = r'(?:typedef\s+)?' + kind + r'\s+' + re.escape(name) + r'\s*\{'
    m = re.search(pattern, content)
    if not m:
        return fields

    start = m.end()
    depth = 1
    pos = start
    while pos < len(content) and depth > 0:
        if content[pos] == '{':
            depth += 1
        elif content[pos] == '}':
            depth -= 1
        pos += 1

    body = content[start:pos - 1]

    if kind == 'enum':
        # Enum values
        for fm in re.finditer(r'(\w+)\s*(?:=\s*[^,}]+)?', body):
            val = fm.group(1).strip()
            if val and not val.startswith('/*') and not val.startswith('//'):
                fields.append(val)
    else:
        # Struct/union fields
        for line in body.split('\n'):
            stripped = line.strip()
            if not stripped or stripped.startswith('/*') or stripped.startswith('*') or stripped.startswith('//'):
                continue
            if stripped.startswith('struct ') and '{' in stripped:
                # Nested struct — skip for now
                continue
            # Match field: type name; or type *name;
            fm = re.match(
                r'(?:const\s+)?(?:unsigned\s+)?(?:volatile\s+)?(?:struct\s+)?'
                r'[\w][\w\s\*]*?\s+\*?(\w+)(?:\[[\w\s\*\+]*\])?\s*;',
                stripped
            )
            if fm:
                fields.append(fm.group(1))

    return fields[:20]  # Limit to avoid bloat


def simplify_params(params):
    """Simplify C function parameters to type-name pairs."""
    if not params or params == 'void':
        return params
    parts = []
    for p in params.split(','):
        p = p.strip()
        if not p:
            continue
        # Remove default values
        p = p.split('=')[0].strip()
        # Extract just type and name
        tokens = p.split()
        if tokens:
            parts.append(p)
    return ', '.join(parts)


def regex_parse_python(filepath, content):
    """Parse a Python file using regex."""
    functions = []
    types = []

    for m in re.finditer(
        r'^(class)\s+(\w+)(?:\(([^)]*)\))?\s*:', content, re.MULTILINE
    ):
        types.append({
            'kind': 'class',
            'name': m.group(2),
            'fields': [m.group(3)] if m.group(3) else [],
        })

    for m in re.finditer(
        r'^(\s*)def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([\w\[\],\s\.]+))?\s*:',
        content, re.MULTILINE
    ):
        indent = m.group(1)
        name = m.group(2)
        params = m.group(3).strip()
        ret = m.group(4)
        functions.append({
            'name': name,
            'return_type': ret.strip() if ret else None,
            'params': params,
            'is_declaration': False,
            'is_static': False,
            'is_method': len(indent) > 0,
            'calls': [],
        })

    return functions, types


def regex_parse_go(filepath, content):
    """Parse a Go file using regex."""
    functions = []
    types = []

    # Functions
    for m in re.finditer(
        r'^func\s+(?:\((\w+)\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)(?:\s*(?:\(([^)]*)\)|(\w[\w\.\*]*)))?\s*\{',
        content, re.MULTILINE
    ):
        receiver_name = m.group(1)
        receiver_type = m.group(2)
        name = m.group(3)
        params = m.group(4).strip()
        ret = m.group(5) or m.group(6) or ''
        full_name = name
        if receiver_type:
            full_name = receiver_type + '.' + name
        functions.append({
            'name': full_name,
            'return_type': ret.strip() if ret else None,
            'params': params,
            'is_declaration': False,
            'is_static': False,
            'calls': [],
        })

    # Types
    for m in re.finditer(r'^type\s+(\w+)\s+(struct|interface)\s*\{', content, re.MULTILINE):
        types.append({
            'kind': m.group(2),
            'name': m.group(1),
            'fields': [],
        })

    return functions, types


def regex_parse_rust(filepath, content):
    """Parse a Rust file using regex."""
    functions = []
    types = []

    for m in re.finditer(
        r'(?:pub(?:\([\w:]+\))?\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)(?:\s*->\s*([\w\[\]<>,\s&\'\*:]+))?\s*(?:where[^{]*)?\{',
        content
    ):
        functions.append({
            'name': m.group(1),
            'return_type': m.group(3).strip() if m.group(3) else None,
            'params': m.group(2).strip(),
            'is_declaration': False,
            'is_static': False,
            'calls': [],
        })

    for m in re.finditer(r'(?:pub\s+)?(?:struct|enum)\s+(\w+)', content):
        types.append({
            'kind': 'struct',
            'name': m.group(1),
            'fields': [],
        })

    return functions, types


def regex_parse_js(filepath, content):
    """Parse a JavaScript/TypeScript file using regex."""
    functions = []
    types = []

    # Regular functions
    for m in re.finditer(
        r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)',
        content
    ):
        functions.append({
            'name': m.group(1),
            'return_type': None,
            'params': m.group(2).strip(),
            'is_declaration': False,
            'is_static': False,
            'calls': [],
        })

    # Arrow / const functions
    for m in re.finditer(
        r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|(\w+))\s*(?::\s*[\w<>\[\],\s]+)?\s*=>',
        content
    ):
        functions.append({
            'name': m.group(1),
            'return_type': None,
            'params': '',
            'is_declaration': False,
            'is_static': False,
            'calls': [],
        })

    # Classes
    for m in re.finditer(r'(?:export\s+)?class\s+(\w+)', content):
        types.append({
            'kind': 'class',
            'name': m.group(1),
            'fields': [],
        })

    # TypeScript interfaces
    for m in re.finditer(r'(?:export\s+)?interface\s+(\w+)', content):
        types.append({
            'kind': 'interface',
            'name': m.group(1),
            'fields': [],
        })

    return functions, types


# ═══════════════════════════════════════════════════════════════════
# Tree-sitter parsers (accurate AST parsing)
# ═══════════════════════════════════════════════════════════════════

def ts_create_parser(lang_module):
    """Create a tree-sitter parser for the given language module."""
    try:
        lang = Language(lang_module.language())
        p = Parser(lang)
        return p
    except Exception:
        return None


def ts_parse_c(filepath, content):
    """Parse C file using tree-sitter."""
    if not HAS_TS_C:
        return None, None

    parser = ts_create_parser(_tsc)
    if not parser:
        return None, None

    tree = parser.parse(content.encode('utf-8'))
    root = tree.root_node

    functions = []
    types = []

    for node in walk_tree(root):
        if node.type == 'function_definition':
            func = extract_ts_c_function(node, content)
            if func:
                # Extract calls from function body
                body = node.child_by_field_name('body')
                if body:
                    calls = set()
                    for call_node in walk_tree(body):
                        if call_node.type == 'call_expression':
                            fn_node = call_node.child_by_field_name('function')
                            if fn_node:
                                callee = content[fn_node.start_byte:fn_node.end_byte]
                                if callee not in C_KEYWORDS and callee != func['name']:
                                    calls.add(callee)
                    func['calls'] = sorted(calls)
                functions.append(func)

        elif node.type == 'declaration':
            # Function declarations in headers
            declarator = node.child_by_field_name('declarator')
            if declarator and declarator.type == 'function_declarator':
                func = extract_ts_c_declaration(node, content)
                if func:
                    functions.append(func)

        elif node.type in ('struct_specifier', 'union_specifier', 'enum_specifier'):
            typ = extract_ts_c_type(node, content)
            if typ:
                types.append(typ)

        elif node.type == 'type_definition':
            # typedef
            for child in node.children:
                if child.type in ('struct_specifier', 'union_specifier', 'enum_specifier'):
                    typ = extract_ts_c_type(child, content)
                    if typ:
                        types.append(typ)

    return functions, types


def extract_ts_c_function(node, content):
    """Extract function info from a tree-sitter function_definition node."""
    declarator = node.child_by_field_name('declarator')
    ret_type_node = node.child_by_field_name('type')

    if not declarator:
        return None

    # Get function name
    name_node = declarator
    while name_node and name_node.type != 'identifier':
        if name_node.type == 'function_declarator':
            name_node = name_node.child_by_field_name('declarator')
        elif name_node.type == 'pointer_declarator':
            name_node = name_node.child_by_field_name('declarator')
        else:
            break

    if not name_node or name_node.type != 'identifier':
        return None

    name = content[name_node.start_byte:name_node.end_byte]
    ret_type = content[ret_type_node.start_byte:ret_type_node.end_byte] if ret_type_node else ''

    # Get parameters
    params_node = None
    for child in walk_tree(declarator):
        if child.type == 'parameter_list':
            params_node = child
            break

    params = content[params_node.start_byte:params_node.end_byte] if params_node else '()'
    params = params.strip('()')

    # Check if static
    is_static = False
    for child in node.children:
        if child.type == 'storage_class_specifier':
            spec = content[child.start_byte:child.end_byte]
            if spec == 'static':
                is_static = True

    return {
        'name': name,
        'return_type': ret_type.strip(),
        'params': params.strip(),
        'is_declaration': False,
        'is_static': is_static,
        'calls': [],
    }


def extract_ts_c_declaration(node, content):
    """Extract function declaration info from a tree-sitter declaration node."""
    declarator = node.child_by_field_name('declarator')
    ret_type_node = node.child_by_field_name('type')

    if not declarator or declarator.type != 'function_declarator':
        return None

    name_node = declarator.child_by_field_name('declarator')
    if not name_node:
        return None

    while name_node and name_node.type == 'pointer_declarator':
        name_node = name_node.child_by_field_name('declarator')

    if not name_node or name_node.type != 'identifier':
        return None

    name = content[name_node.start_byte:name_node.end_byte]
    ret_type = content[ret_type_node.start_byte:ret_type_node.end_byte] if ret_type_node else ''

    params_node = declarator.child_by_field_name('parameters')
    params = content[params_node.start_byte:params_node.end_byte] if params_node else '()'
    params = params.strip('()')

    return {
        'name': name,
        'return_type': ret_type.strip(),
        'params': params.strip(),
        'is_declaration': True,
        'is_static': False,
        'calls': [],
    }


def extract_ts_c_type(node, content):
    """Extract struct/union/enum type info from a tree-sitter node."""
    name_node = node.child_by_field_name('name')
    if not name_node:
        return None

    name = content[name_node.start_byte:name_node.end_byte]
    kind = node.type.replace('_specifier', '')

    body = node.child_by_field_name('body')
    fields = []
    if body:
        for child in body.children:
            if child.type == 'field_declaration':
                decl = child.child_by_field_name('declarator')
                if decl:
                    field_name = content[decl.start_byte:decl.end_byte]
                    fields.append(field_name.strip('*').strip())
            elif child.type == 'enumerator':
                name_child = child.child_by_field_name('name')
                if name_child:
                    fields.append(content[name_child.start_byte:name_child.end_byte])

    return {
        'kind': kind,
        'name': name,
        'fields': fields[:20],
    }


def walk_tree(node):
    """Walk a tree-sitter tree yielding all nodes."""
    yield node
    for child in node.children:
        for n in walk_tree(child):
            yield n


# ═══════════════════════════════════════════════════════════════════
# Main parsing dispatcher
# ═══════════════════════════════════════════════════════════════════

def parse_file(filepath, content, ext):
    """Parse a source file and return (functions, types).
    Uses tree-sitter if available, falls back to regex.
    """
    if ext in C_EXTENSIONS:
        if HAS_TS_C:
            funcs, types = ts_parse_c(filepath, content)
            if funcs is not None:
                return funcs, types
        # Regex fallback
        if ext == '.h':
            funcs, types, macros = regex_parse_c_header(filepath, content)
            return funcs, types
        else:
            return regex_parse_c_source(filepath, content)

    elif ext in CPP_EXTENSIONS:
        if HAS_TS_CPP:
            # Use C++ tree-sitter parser — similar API to C
            funcs, types = ts_parse_c(filepath, content)  # C parser works for basic C++ too
            if funcs is not None:
                return funcs, types
        if ext in ('.hpp', '.hxx', '.hh'):
            funcs, types, macros = regex_parse_c_header(filepath, content)
            return funcs, types
        else:
            return regex_parse_c_source(filepath, content)

    elif ext in PY_EXTENSIONS:
        return regex_parse_python(filepath, content)

    elif ext in JS_EXTENSIONS:
        return regex_parse_js(filepath, content)

    elif ext in GO_EXTENSIONS:
        return regex_parse_go(filepath, content)

    elif ext in RUST_EXTENSIONS:
        return regex_parse_rust(filepath, content)

    return [], []


# ═══════════════════════════════════════════════════════════════════
# Output generators
# ═══════════════════════════════════════════════════════════════════

def generate_compact_notation(project_dir, file_data, all_functions):
    """Generate the compact notation format for structural-map.md."""
    lines = []

    # Build called_by index
    called_by = defaultdict(set)
    for fpath, data in file_data.items():
        for func in data['functions']:
            for callee in func.get('calls', []):
                called_by[callee].add(func['name'])

    for fpath in sorted(file_data.keys()):
        data = file_data[fpath]
        if not data['functions'] and not data['types']:
            continue

        lines.append('[{}]'.format(fpath))

        for func in data['functions']:
            # Build signature
            ret = func.get('return_type', '')
            params = func.get('params', '')
            static_prefix = 'static ' if func.get('is_static') else ''
            ret_str = ' -> {}'.format(ret) if ret else ''

            lines.append('  fn {}{}{}'.format(
                static_prefix, func['name'],
                '({})'.format(params) + ret_str
            ))

            calls = func.get('calls', [])
            if calls:
                # Only show calls to functions that exist in the project
                known_calls = [c for c in calls if c in all_functions]
                if known_calls:
                    lines.append('    calls: {}'.format(', '.join(known_calls[:15])))

            cb = called_by.get(func['name'], set())
            if cb:
                lines.append('    called_by: {}'.format(', '.join(sorted(cb)[:15])))

        for typ in data['types']:
            kind = typ.get('kind', 'struct')
            name = typ.get('name', '')
            fields = typ.get('fields', [])
            if fields:
                lines.append('  {} {} {{ {} }}'.format(kind, name, ', '.join(fields[:10])))
            else:
                lines.append('  {} {}'.format(kind, name))

        lines.append('')

    return '\n'.join(lines)


def generate_mermaid(project_dir, file_data, all_functions, subsystem_map):
    """Generate Mermaid call graph diagram."""
    lines = ['graph TD']

    # Group functions by subsystem
    subsystem_functions = defaultdict(list)
    for fpath, data in file_data.items():
        subsystem = subsystem_map.get(fpath, 'other')
        for func in data['functions']:
            if not func.get('is_static') and not func.get('is_declaration'):
                subsystem_functions[subsystem].append(func)

    # Collect edges
    edges = []
    for fpath, data in file_data.items():
        for func in data['functions']:
            if func.get('is_declaration'):
                continue
            for callee in func.get('calls', []):
                if callee in all_functions:
                    edges.append((func['name'], callee))

    if not edges:
        return 'graph TD\n    no_functions[No call graph data available]'

    # Build subgraphs per subsystem
    func_to_subsystem = {}
    for sub, funcs in subsystem_functions.items():
        for f in funcs:
            func_to_subsystem[f['name']] = sub

    # Sanitize node IDs for Mermaid (replace non-alphanumeric with _)
    def mermaid_id(name):
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    # Write subgraphs
    rendered_funcs = set()
    for sub in sorted(subsystem_functions.keys()):
        funcs = subsystem_functions[sub]
        # Only include functions that have edges
        func_names_in_sub = {f['name'] for f in funcs}
        active_funcs = set()
        for src, dst in edges:
            if src in func_names_in_sub:
                active_funcs.add(src)
            if dst in func_names_in_sub:
                active_funcs.add(dst)

        if not active_funcs:
            continue

        sub_label = sub.replace('/', '_').replace('.', '_')
        lines.append('    subgraph {}[{}]'.format(mermaid_id(sub_label), sub))
        for fname in sorted(active_funcs):
            mid = mermaid_id(fname)
            lines.append('        {}[{}]'.format(mid, fname))
            rendered_funcs.add(fname)
        lines.append('    end')

    # Write edges
    for src, dst in sorted(set(edges)):
        if src in rendered_funcs and dst in rendered_funcs:
            lines.append('    {} --> {}'.format(mermaid_id(src), mermaid_id(dst)))

    return '\n'.join(lines)


def generate_cross_subsystem_mermaid(file_data, all_functions, subsystem_map):
    """Generate Mermaid diagram showing only cross-subsystem calls."""
    lines = ['graph TD']

    func_to_subsystem = {}
    for fpath, data in file_data.items():
        sub = subsystem_map.get(fpath, 'other')
        for func in data['functions']:
            func_to_subsystem[func['name']] = sub

    cross_edges = []
    for fpath, data in file_data.items():
        for func in data['functions']:
            if func.get('is_declaration'):
                continue
            src_sub = func_to_subsystem.get(func['name'], 'other')
            for callee in func.get('calls', []):
                if callee in func_to_subsystem:
                    dst_sub = func_to_subsystem[callee]
                    if src_sub != dst_sub:
                        cross_edges.append((func['name'], callee, src_sub, dst_sub))

    if not cross_edges:
        return 'graph TD\n    no_cross[No cross-subsystem calls detected]'

    # Group by subsystem pairs
    rendered = set()

    def mermaid_id(name):
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    # Collect all subsystems that participate in cross-calls
    active_subs = set()
    for src, dst, ssub, dsub in cross_edges:
        active_subs.add(ssub)
        active_subs.add(dsub)

    # Build subgraphs
    sub_funcs = defaultdict(set)
    for src, dst, ssub, dsub in cross_edges:
        sub_funcs[ssub].add(src)
        sub_funcs[dsub].add(dst)

    for sub in sorted(active_subs):
        sub_label = sub.replace('/', '_').replace('.', '_')
        lines.append('    subgraph {}[{}]'.format(mermaid_id(sub_label), sub))
        for fname in sorted(sub_funcs[sub]):
            lines.append('        {}[{}]'.format(mermaid_id(fname), fname))
        lines.append('    end')

    # Edges
    for src, dst, ssub, dsub in sorted(set((s, d, ss, ds) for s, d, ss, ds in cross_edges)):
        edge_key = (src, dst)
        if edge_key not in rendered:
            rendered.add(edge_key)
            lines.append('    {} --> {}'.format(mermaid_id(src), mermaid_id(dst)))

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def detect_subsystem(filepath):
    """Map a file path to its subsystem based on top-level directory."""
    parts = filepath.split(os.sep)
    if len(parts) <= 1:
        return 'root'
    return parts[0]


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Generate structural map for Claude Code awareness system'
    )
    parser.add_argument('project_dir', help='Project directory to analyze')
    parser.add_argument('--mermaid', action='store_true',
                        help='Also generate Mermaid call graph output')
    parser.add_argument('--cross-subsystem', action='store_true',
                        help='Generate cross-subsystem-only Mermaid diagram')
    parser.add_argument('--output', '-o', metavar='DIR',
                        help='Output directory (default: .claude/awareness/)')
    parser.add_argument('--stdout', action='store_true',
                        help='Print to stdout instead of writing files')
    parser.add_argument('--json', action='store_true',
                        help='Output raw parsed data as JSON')
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
        if HAS_TREE_SITTER:
            langs = []
            if HAS_TS_C:
                langs.append('C')
            if HAS_TS_CPP:
                langs.append('C++')
            if HAS_TS_PYTHON:
                langs.append('Python')
            if HAS_TS_JS:
                langs.append('JS')
            if HAS_TS_GO:
                langs.append('Go')
            if HAS_TS_RUST:
                langs.append('Rust')
            print("tree-sitter available for: {}".format(', '.join(langs) if langs else 'none'),
                  file=sys.stderr)
        else:
            print("tree-sitter not available, using regex fallback", file=sys.stderr)

    # Scan source files
    file_data = OrderedDict()
    all_functions = set()
    file_count = 0
    subsystem_map = {}

    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = sorted([d for d in dirs if not should_skip(d)])
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALL_EXTENSIONS:
                continue

            filepath = os.path.join(root, name)
            rel_path = os.path.relpath(filepath, project_dir)
            content = read_file(filepath)
            if not content:
                continue

            if args.verbose:
                print("  Parsing {}...".format(rel_path), file=sys.stderr)

            functions, types = parse_file(filepath, content, ext)
            file_data[rel_path] = {
                'functions': functions,
                'types': types,
            }

            for f in functions:
                all_functions.add(f['name'])

            subsystem_map[rel_path] = detect_subsystem(rel_path)
            file_count += 1

    if args.verbose:
        print("Parsed {} files, found {} functions".format(
            file_count, len(all_functions)), file=sys.stderr)

    # Generate outputs
    if args.json:
        # Raw JSON output
        output = {
            'files': {},
            'all_functions': sorted(all_functions),
        }
        for fpath, data in file_data.items():
            output['files'][fpath] = {
                'functions': data['functions'],
                'types': [{'kind': t['kind'], 'name': t['name'], 'fields': t['fields']}
                          for t in data['types']],
            }
        print(json.dumps(output, indent=2))
        return

    # Generate compact notation
    compact = generate_compact_notation(project_dir, file_data, all_functions)
    token_count = estimate_tokens(compact)

    project_name = os.path.basename(project_dir)
    import datetime
    today = datetime.date.today().isoformat()

    header = """# Structural Map — {name}

**Generated**: {date}
**Source files scanned**: {count}
**Approximate token count**: {tokens}
**Parser**: {parser}

## Notation

```
[path/to/file.ext]
  fn function_name(params) -> return_type
    calls: fn1, fn2, fn3
    called_by: fn4, fn5
  struct StructName {{ field1, field2 }}
  enum EnumName {{ VAL1, VAL2 }}
```

- `calls:` lists functions this function directly invokes (project-internal only)
- `called_by:` lists functions that directly invoke this function
- Only PUBLIC / EXPORTED symbols listed unless internal symbols cross file boundaries
- Call graph edges are best-effort — static analysis only

## Map

```
{map}
```

## Cross-File Dependency Summary

```
{deps}
```
""".format(
        name=project_name,
        date=today,
        count=file_count,
        tokens=token_count,
        parser='tree-sitter' if HAS_TREE_SITTER else 'regex',
        map=compact,
        deps=generate_file_deps(file_data, all_functions),
    )

    # Generate Mermaid if requested
    mermaid_output = ''
    if args.mermaid or args.cross_subsystem:
        if args.cross_subsystem:
            mermaid_output = generate_cross_subsystem_mermaid(
                file_data, all_functions, subsystem_map)
        else:
            mermaid_output = generate_mermaid(
                project_dir, file_data, all_functions, subsystem_map)

    if args.stdout:
        print(header)
        if mermaid_output:
            print("\n## Visual Call Graph\n")
            print("```mermaid")
            print(mermaid_output)
            print("```")
        return

    # Write to files
    output_dir = args.output
    if not output_dir:
        output_dir = os.path.join(project_dir, '.claude', 'awareness')

    os.makedirs(output_dir, exist_ok=True)

    map_path = os.path.join(output_dir, 'structural-map.md')
    with open(map_path, 'w') as f:
        f.write(header)
        if mermaid_output:
            f.write("\n## Visual Call Graph\n\n")
            f.write("```mermaid\n")
            f.write(mermaid_output)
            f.write("\n```\n")

    print("Structural map written to {}".format(map_path))
    print("  {} files scanned, {} functions found".format(file_count, len(all_functions)))
    print("  Approximate token count: {}".format(token_count))

    if mermaid_output:
        mermaid_path = os.path.join(output_dir, 'structural-map.mermaid')
        with open(mermaid_path, 'w') as f:
            f.write(mermaid_output)
        print("  Mermaid diagram written to {}".format(mermaid_path))


def generate_file_deps(file_data, all_functions):
    """Generate cross-file dependency summary."""
    lines = []
    file_funcs = {}
    for fpath, data in file_data.items():
        file_funcs[fpath] = {f['name'] for f in data['functions']}

    func_to_file = {}
    for fpath, funcs in file_funcs.items():
        for fname in funcs:
            func_to_file[fname] = fpath

    for fpath, data in sorted(file_data.items()):
        deps = set()
        for func in data['functions']:
            for callee in func.get('calls', []):
                if callee in func_to_file:
                    dep_file = func_to_file[callee]
                    if dep_file != fpath:
                        deps.add(dep_file)
        if deps:
            lines.append('{} -> {}'.format(fpath, ', '.join(sorted(deps))))

    return '\n'.join(lines) if lines else '(no cross-file dependencies detected)'


if __name__ == '__main__':
    main()
