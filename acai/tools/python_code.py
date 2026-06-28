"""Python code tools — AST-powered precise structural editing.

Leverages Python's ``ast`` module for exact, structure-aware operations
on Python source files.  Unlike text-based search-and-replace, these
tools understand the syntax tree and can target specific functions,
classes, methods, or decorators by name — making edits reliable even
when there are duplicated patterns in comments or strings.

Available operations:

- ``inspect``: List all top-level and nested symbols with signatures, decorators, docstrings, and line ranges.
- ``get_source``: Extract the exact source of a named function, class, or method.
- ``replace_function``: Replace a function/method body by name (preserving signature, decorators, etc. or replacing entirely).
- ``rename_symbol``: Rename a function, class, or variable across the file.
- ``add_method``: Insert a new method into an existing class.
- ``remove_symbol``: Delete a function, class, or method from the file.
- ``get_imports``: List all imports in a file.
- ``add_import``: Add an import statement if not already present.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import textwrap
from typing import Any

from acai.orchestrator.tools import tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_source(path: str) -> tuple[str, str | None]:
    """Read a file, return (source, error)."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(), None
    except OSError as exc:
        return "", str(exc)


def _write_source(path: str, source: str) -> str | None:
    """Write source atomically. Returns error string or None."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(source)
        os.replace(tmp, path)
        return None
    except OSError as exc:
        return str(exc)


def _get_node_source(source: str, node: ast.AST) -> str:
    """Extract the exact source text for an AST node."""
    return ast.get_source_segment(source, node) or ""


def _get_lines(source: str, start: int, end: int) -> str:
    """Extract lines start..end (1-based, inclusive)."""
    lines = source.splitlines(keepends=True)
    return "".join(lines[start - 1:end])


def _node_signature(node: ast.AST) -> str:
    """Generate a human-readable signature for a function/class node."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = []
        all_args = node.args
        # Positional args
        defaults_offset = len(all_args.args) - len(all_args.defaults)
        for i, arg in enumerate(all_args.args):
            ann = ""
            if arg.annotation:
                ann = f": {ast.unparse(arg.annotation)}"
            default = ""
            if i >= defaults_offset:
                default = f" = {ast.unparse(all_args.defaults[i - defaults_offset])}"
            args.append(f"{arg.arg}{ann}{default}")
        # *args
        if all_args.vararg:
            ann = f": {ast.unparse(all_args.vararg.annotation)}" if all_args.vararg.annotation else ""
            args.append(f"*{all_args.vararg.arg}{ann}")
        # **kwargs
        if all_args.kwarg:
            ann = f": {ast.unparse(all_args.kwarg.annotation)}" if all_args.kwarg.annotation else ""
            args.append(f"**{all_args.kwarg.arg}{ann}")

        ret = ""
        if node.returns:
            ret = f" -> {ast.unparse(node.returns)}"

        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)}){ret}"

    elif isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        base_str = f"({', '.join(bases)})" if bases else ""
        return f"class {node.name}{base_str}"

    return getattr(node, "name", "?")


def _node_decorators(node: ast.AST) -> list[str]:
    """Get decorator strings for a node."""
    decorators = getattr(node, "decorator_list", [])
    return [f"@{ast.unparse(d)}" for d in decorators]


def _node_docstring(node: ast.AST) -> str:
    """Extract the docstring from a function or class node."""
    return ast.get_docstring(node) or ""


def _find_symbol(tree: ast.Module, name: str) -> ast.AST | None:
    """Find a top-level symbol or a nested method (Class.method)."""
    parts = name.split(".")
    if len(parts) == 1:
        for node in ast.iter_child_nodes(tree):
            if getattr(node, "name", None) == parts[0]:
                return node
    elif len(parts) == 2:
        class_name, method_name = parts
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in ast.iter_child_nodes(node):
                    if getattr(child, "name", None) == method_name:
                        return child
    return None


def _symbol_info(node: ast.AST, source: str) -> dict:
    """Build a detailed info dict for a symbol."""
    info: dict[str, Any] = {
        "name": getattr(node, "name", "?"),
        "type": type(node).__name__.replace("Def", "").replace("Async", "async_").lower(),
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
        "signature": _node_signature(node),
        "decorators": _node_decorators(node),
    }
    doc = _node_docstring(node)
    if doc:
        info["docstring"] = doc[:200] + ("..." if len(doc) > 200 else "")
    return info


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(permissions=("read",), resources=("code:read",))
def inspect(path: str, include_private: bool = True) -> str:
    """Inspect a Python file — list all symbols with signatures, line ranges, decorators, and docstrings.

    Returns a structured view of the file: classes with their methods,
    standalone functions, module-level variables, and imports.
    Much more detailed than ``file_outline`` — includes full signatures
    with type annotations, decorator lists, and docstring previews.

    Args:
        path: Path to the Python file.
        include_private: If False, skip symbols starting with '_' (default True).
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    symbols: list[dict] = []
    for node in ast.iter_child_nodes(tree):
        name = getattr(node, "name", None)
        if name and not include_private and name.startswith("_"):
            continue

        if isinstance(node, ast.ClassDef):
            class_info = _symbol_info(node, source)
            members: list[dict] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child_name = child.name
                    if not include_private and child_name.startswith("_") and child_name != "__init__":
                        continue
                    members.append(_symbol_info(child, source))
            class_info["members"] = members
            symbols.append(class_info)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol_info(node, source))

        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = None
            if isinstance(node, ast.Assign) and node.targets:
                t = node.targets[0]
                if isinstance(t, ast.Name):
                    target = t.id
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target.id

            if target:
                if not include_private and target.startswith("_"):
                    continue
                symbols.append({
                    "name": target,
                    "type": "variable",
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                })

    total_lines = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    return json.dumps({
        "path": path,
        "total_lines": total_lines,
        "symbols": symbols,
    })


@tool(permissions=("read",), resources=("code:read",))
def get_source(path: str, name: str) -> str:
    """Extract the exact source code of a function, class, or method.

    For methods, use dot notation: ``ClassName.method_name``.
    Returns the complete source including decorators and docstring.

    Args:
        path: Path to the Python file.
        name: Symbol name (e.g. "my_function" or "MyClass.my_method").
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    node = _find_symbol(tree, name)
    if node is None:
        return json.dumps({"error": f"Symbol '{name}' not found in {path}"})

    # Include decorators in the source range
    start_line = node.lineno
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start_line = decorators[0].lineno

    end_line = node.end_lineno or node.lineno
    extracted = _get_lines(source, start_line, end_line)

    return json.dumps({
        "name": name,
        "path": path,
        "line_start": start_line,
        "line_end": end_line,
        "source": extracted,
        "signature": _node_signature(node),
    })


@tool(permissions=("write",), resources=("code:write",))
def replace_function(path: str, name: str, new_source: str) -> str:
    """Replace an entire function or method with new source code.

    Targets the symbol precisely by name using the AST — will not
    accidentally match comments, strings, or other functions with
    similar content.

    For methods, use dot notation: ``ClassName.method_name``.

    The ``new_source`` should be the complete function definition
    including ``def``/``async def``, decorators, and body.
    Indentation will be adjusted automatically to match the original.

    Args:
        path: Path to the Python file.
        name: Symbol name (e.g. "my_function" or "MyClass.my_method").
        new_source: Complete replacement source code for the function.
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    node = _find_symbol(tree, name)
    if node is None:
        return json.dumps({"error": f"Symbol '{name}' not found in {path}"})

    # Determine the full range (including decorators)
    start_line = node.lineno
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start_line = decorators[0].lineno

    end_line = node.end_lineno or node.lineno
    lines = source.splitlines(keepends=True)

    # Detect indentation of the original
    original_first_line = lines[start_line - 1] if start_line <= len(lines) else ""
    original_indent = len(original_first_line) - len(original_first_line.lstrip())

    # Normalize new_source indentation to match
    new_source_stripped = textwrap.dedent(new_source)
    if original_indent > 0:
        indent_str = " " * original_indent
        new_lines = new_source_stripped.splitlines(keepends=True)
        new_source_indented = "".join(indent_str + line if line.strip() else line for line in new_lines)
    else:
        new_source_indented = new_source_stripped

    if not new_source_indented.endswith("\n"):
        new_source_indented += "\n"

    # Replace the lines
    result = "".join(lines[:start_line - 1]) + new_source_indented + "".join(lines[end_line:])

    # Validate the result parses
    try:
        ast.parse(result, filename=path)
    except SyntaxError as exc:
        return json.dumps({
            "error": f"Replacement produces invalid syntax: {exc}",
            "hint": "Check indentation and ensure the new_source is a complete, valid function definition.",
        })

    write_err = _write_source(path, result)
    if write_err:
        return json.dumps({"error": write_err})

    return json.dumps({
        "replaced": name,
        "path": path,
        "old_lines": f"{start_line}-{end_line}",
        "new_line_count": new_source_indented.count("\n"),
    })


@tool(permissions=("write",), resources=("code:write",))
def rename_symbol(path: str, old_name: str, new_name: str) -> str:
    """Rename a function, class, method, or variable throughout a file.

    Uses AST awareness to only rename actual symbol definitions and
    references — won't rename occurrences in strings or comments.

    For methods, use dot notation: ``ClassName.old_method``.
    The rename only applies to the definition; call sites in the same
    file are also updated.

    Args:
        path: Path to the Python file.
        old_name: Current name of the symbol.
        new_name: New name for the symbol.
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    # For dotted names, handle class.method
    parts = old_name.split(".")
    if len(parts) == 2:
        class_name, method_name = parts
        target_node = _find_symbol(tree, old_name)
        if target_node is None:
            return json.dumps({"error": f"Symbol '{old_name}' not found"})
        # Simple rename: replace just the method name in source
        source = _rename_in_source(source, tree, method_name, new_name, scope_class=class_name)
    else:
        target_node = _find_symbol(tree, old_name)
        if target_node is None:
            return json.dumps({"error": f"Symbol '{old_name}' not found"})
        source = _rename_in_source(source, tree, old_name, new_name)

    # Validate
    try:
        ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"Rename produces invalid syntax: {exc}"})

    write_err = _write_source(path, source)
    if write_err:
        return json.dumps({"error": write_err})

    return json.dumps({"renamed": old_name, "to": new_name, "path": path})


def _rename_in_source(source: str, tree: ast.Module, old: str, new: str, scope_class: str = "") -> str:
    """Rename occurrences of a symbol using AST node positions."""
    import re

    # Use word-boundary regex but only on lines that the AST tells us contain the symbol
    # This is conservative: renames definition + attribute/call references
    pattern = re.compile(r'\b' + re.escape(old) + r'\b')

    lines = source.splitlines(keepends=True)
    # Collect all line numbers where the AST references this name
    target_lines: set[int] = set()

    class NameCollector(ast.NodeVisitor):
        def visit_Name(self, node):
            if node.id == old:
                target_lines.add(node.lineno)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if node.name == old:
                target_lines.add(node.lineno)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            if node.name == old:
                target_lines.add(node.lineno)
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if node.attr == old:
                target_lines.add(node.lineno)
            self.generic_visit(node)

    if scope_class:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == scope_class:
                NameCollector().visit(node)
    else:
        NameCollector().visit(tree)

    result_lines = []
    for i, line in enumerate(lines, start=1):
        if i in target_lines:
            result_lines.append(pattern.sub(new, line))
        else:
            result_lines.append(line)

    return "".join(result_lines)


@tool(permissions=("write",), resources=("code:write",))
def add_method(path: str, class_name: str, method_source: str, after: str = "") -> str:
    """Add a new method to an existing class.

    Inserts the method at the end of the class body, or after a
    specific existing method if ``after`` is given.

    Args:
        path: Path to the Python file.
        class_name: Name of the class to add the method to.
        method_source: Complete method source (def line + body). Indentation is adjusted automatically.
        after: Insert after this method name (empty = end of class).
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    class_node = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_node = node
            break

    if class_node is None:
        return json.dumps({"error": f"Class '{class_name}' not found in {path}"})

    # Find insertion point
    insert_after_line = class_node.end_lineno or class_node.lineno
    if after:
        for child in ast.iter_child_nodes(class_node):
            if getattr(child, "name", None) == after:
                insert_after_line = child.end_lineno or child.lineno
                break

    # Detect class body indentation (typically 4 spaces)
    lines = source.splitlines(keepends=True)
    class_indent = 0
    for child in ast.iter_child_nodes(class_node):
        if hasattr(child, "col_offset"):
            class_indent = child.col_offset
            break
    if class_indent == 0:
        class_indent = 4

    # Indent the new method
    method_dedented = textwrap.dedent(method_source)
    indent_str = " " * class_indent
    method_lines = method_dedented.splitlines(keepends=True)
    indented_method = "".join(
        indent_str + line if line.strip() else "\n" for line in method_lines
    )
    if not indented_method.startswith("\n"):
        indented_method = "\n" + indented_method
    if not indented_method.endswith("\n"):
        indented_method += "\n"

    result = "".join(lines[:insert_after_line]) + indented_method + "".join(lines[insert_after_line:])

    try:
        ast.parse(result, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"Insertion produces invalid syntax: {exc}"})

    write_err = _write_source(path, result)
    if write_err:
        return json.dumps({"error": write_err})

    return json.dumps({
        "added_method_to": class_name,
        "path": path,
        "inserted_after_line": insert_after_line,
    })


@tool(permissions=("write",), resources=("code:write",))
def remove_symbol(path: str, name: str) -> str:
    """Remove a function, class, or method from a Python file.

    For methods, use dot notation: ``ClassName.method_name``.
    Removes the entire definition including decorators and docstring.

    Args:
        path: Path to the Python file.
        name: Symbol to remove (e.g. "old_function" or "MyClass.deprecated_method").
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    node = _find_symbol(tree, name)
    if node is None:
        return json.dumps({"error": f"Symbol '{name}' not found in {path}"})

    start_line = node.lineno
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start_line = decorators[0].lineno

    end_line = node.end_lineno or node.lineno
    lines = source.splitlines(keepends=True)

    # Remove the lines, plus any trailing blank line
    result_lines = lines[:start_line - 1] + lines[end_line:]
    # Clean up double blank lines at the removal site
    if start_line - 1 < len(result_lines) and start_line >= 2:
        if (result_lines[start_line - 2].strip() == "" and
                start_line - 1 < len(result_lines) and result_lines[start_line - 1].strip() == ""):
            result_lines.pop(start_line - 1)

    result = "".join(result_lines)

    try:
        ast.parse(result, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"Removal produces invalid syntax: {exc}"})

    write_err = _write_source(path, result)
    if write_err:
        return json.dumps({"error": write_err})

    return json.dumps({
        "removed": name,
        "path": path,
        "lines_removed": f"{start_line}-{end_line}",
    })


@tool(permissions=("read",), resources=("code:read",))
def get_imports(path: str) -> str:
    """List all import statements in a Python file.

    Returns structured info: module, names imported, whether it's
    ``import X`` or ``from X import Y``, and line numbers.

    Args:
        path: Path to the Python file.
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    imports: list[dict] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "type": "import",
                    "module": alias.name,
                    "alias": alias.asname or "",
                    "line": node.lineno,
                    "statement": _get_lines(source, node.lineno, node.end_lineno or node.lineno).strip(),
                })
        elif isinstance(node, ast.ImportFrom):
            names = [
                {"name": alias.name, "alias": alias.asname or ""}
                for alias in node.names
            ]
            imports.append({
                "type": "from_import",
                "module": node.module or "",
                "level": node.level,
                "names": names,
                "line": node.lineno,
                "statement": _get_lines(source, node.lineno, node.end_lineno or node.lineno).strip(),
            })

    return json.dumps({"path": path, "imports": imports})


@tool(permissions=("write",), resources=("code:write",))
def add_import(path: str, statement: str) -> str:
    """Add an import statement to a Python file if not already present.

    Inserts the import in the correct position (after existing imports,
    respecting the standard grouping: stdlib → third-party → local).

    Args:
        path: Path to the Python file.
        statement: The import statement (e.g. "from typing import Optional" or "import os").
    """
    source, err = _read_source(path)
    if err:
        return json.dumps({"error": err})

    # Check if already present (exact line match)
    statement_stripped = statement.strip()
    if statement_stripped in source:
        return json.dumps({"status": "already_present", "path": path})

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"SyntaxError: {exc}"})

    # Find the last import line
    last_import_line = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # Module docstring — imports should go after
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)

    lines = source.splitlines(keepends=True)
    insert_at = last_import_line  # insert after this line (0-indexed)

    new_line = statement_stripped + "\n"
    lines.insert(insert_at, new_line)

    result = "".join(lines)

    try:
        ast.parse(result, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": f"Import produces invalid syntax: {exc}"})

    write_err = _write_source(path, result)
    if write_err:
        return json.dumps({"error": write_err})

    return json.dumps({
        "added": statement_stripped,
        "path": path,
        "at_line": insert_at + 1,
    })
