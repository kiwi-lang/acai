"""Tests for acai.tools.python_code — AST-powered Python code editing tools."""

from __future__ import annotations

import json
import os
import textwrap

import pytest

from acai.tools.python_code import (
    add_import,
    add_method,
    get_imports,
    get_source,
    inspect,
    remove_symbol,
    rename_symbol,
    replace_function,
)


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample Python file for testing."""
    code = textwrap.dedent("""\
        \"\"\"Sample module for testing.\"\"\"

        from __future__ import annotations

        import os
        import sys
        from typing import Optional, List

        _PRIVATE_VAR = 42
        PUBLIC_VAR: str = "hello"


        def simple_function(x: int, y: int = 0) -> int:
            \"\"\"Add two numbers.\"\"\"
            return x + y


        def another_function():
            pass


        async def async_func(data: list[str]) -> None:
            \"\"\"Process data asynchronously.\"\"\"
            for item in data:
                print(item)


        class MyClass:
            \"\"\"A sample class.\"\"\"

            def __init__(self, name: str):
                self.name = name

            def greet(self) -> str:
                \"\"\"Return a greeting.\"\"\"
                return f"Hello, {self.name}!"

            async def fetch(self, url: str) -> bytes:
                \"\"\"Fetch data from url.\"\"\"
                return b""

            def _private_method(self):
                pass


        class SubClass(MyClass):
            \"\"\"A subclass.\"\"\"

            def extra(self):
                return True
    """)
    p = tmp_path / "sample.py"
    p.write_text(code)
    return str(p)


@pytest.fixture
def minimal_file(tmp_path):
    """Minimal Python file."""
    code = textwrap.dedent("""\
        import os

        def foo():
            return 1
    """)
    p = tmp_path / "minimal.py"
    p.write_text(code)
    return str(p)


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


class TestInspect:
    def test_basic(self, sample_file):
        result = json.loads(inspect(sample_file))
        assert "symbols" in result
        assert result["path"] == sample_file
        assert result["total_lines"] > 0

        names = [s["name"] for s in result["symbols"]]
        assert "simple_function" in names
        assert "another_function" in names
        assert "async_func" in names
        assert "MyClass" in names
        assert "SubClass" in names
        assert "_PRIVATE_VAR" in names
        assert "PUBLIC_VAR" in names

    def test_function_signatures(self, sample_file):
        result = json.loads(inspect(sample_file))
        funcs = [s for s in result["symbols"] if s["type"] == "function"]
        simple = next(f for f in funcs if f["name"] == "simple_function")
        assert "x: int" in simple["signature"]
        assert "y: int = 0" in simple["signature"]
        assert "-> int" in simple["signature"]
        assert "def simple_function" in simple["signature"]

    def test_async_function(self, sample_file):
        result = json.loads(inspect(sample_file))
        funcs = [s for s in result["symbols"] if s["name"] == "async_func"]
        assert len(funcs) == 1
        assert "async_" in funcs[0]["type"] or "async def" in funcs[0]["signature"]

    def test_class_members(self, sample_file):
        result = json.loads(inspect(sample_file))
        cls = next(s for s in result["symbols"] if s["name"] == "MyClass")
        assert "members" in cls
        member_names = [m["name"] for m in cls["members"]]
        assert "__init__" in member_names
        assert "greet" in member_names
        assert "fetch" in member_names
        assert "_private_method" in member_names

    def test_docstrings(self, sample_file):
        result = json.loads(inspect(sample_file))
        simple = next(s for s in result["symbols"] if s["name"] == "simple_function")
        assert "docstring" in simple
        assert "Add two numbers" in simple["docstring"]

    def test_exclude_private(self, sample_file):
        result = json.loads(inspect(sample_file, include_private=False))
        names = [s["name"] for s in result["symbols"]]
        assert "_PRIVATE_VAR" not in names
        assert "PUBLIC_VAR" in names
        # __init__ is always included
        cls = next(s for s in result["symbols"] if s["name"] == "MyClass")
        member_names = [m["name"] for m in cls["members"]]
        assert "__init__" in member_names
        assert "_private_method" not in member_names

    def test_class_inheritance(self, sample_file):
        result = json.loads(inspect(sample_file))
        sub = next(s for s in result["symbols"] if s["name"] == "SubClass")
        assert "MyClass" in sub["signature"]

    def test_file_not_found(self, tmp_path):
        result = json.loads(inspect(str(tmp_path / "nonexistent.py")))
        assert "error" in result

    def test_syntax_error(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def foo(:\n    pass\n")
        result = json.loads(inspect(str(bad)))
        assert "error" in result
        assert "SyntaxError" in result["error"]


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


class TestGetSource:
    def test_function(self, sample_file):
        result = json.loads(get_source(sample_file, "simple_function"))
        assert "source" in result
        assert "def simple_function" in result["source"]
        assert "return x + y" in result["source"]
        assert result["name"] == "simple_function"
        assert result["line_start"] > 0
        assert result["line_end"] >= result["line_start"]

    def test_method(self, sample_file):
        result = json.loads(get_source(sample_file, "MyClass.greet"))
        assert "source" in result
        assert "def greet" in result["source"]
        assert "Hello" in result["source"]

    def test_class(self, sample_file):
        result = json.loads(get_source(sample_file, "MyClass"))
        assert "source" in result
        assert "class MyClass" in result["source"]
        assert "__init__" in result["source"]
        assert "greet" in result["source"]

    def test_not_found(self, sample_file):
        result = json.loads(get_source(sample_file, "nonexistent"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_method_not_found(self, sample_file):
        result = json.loads(get_source(sample_file, "MyClass.nonexistent"))
        assert "error" in result

    def test_async_function(self, sample_file):
        result = json.loads(get_source(sample_file, "async_func"))
        assert "async def async_func" in result["source"]


# ---------------------------------------------------------------------------
# replace_function
# ---------------------------------------------------------------------------


class TestReplaceFunction:
    def test_replace_simple(self, sample_file):
        new_src = textwrap.dedent("""\
            def simple_function(x: int, y: int = 0) -> int:
                \"\"\"Multiply instead of add.\"\"\"
                return x * y
        """)
        result = json.loads(replace_function(sample_file, "simple_function", new_src))
        assert "error" not in result
        assert result["replaced"] == "simple_function"

        # Verify file content
        with open(sample_file) as f:
            content = f.read()
        assert "return x * y" in content
        assert "return x + y" not in content

    def test_replace_method(self, sample_file):
        new_src = textwrap.dedent("""\
            def greet(self) -> str:
                return f"Hi, {self.name}!"
        """)
        result = json.loads(replace_function(sample_file, "MyClass.greet", new_src))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "Hi, {self.name}!" in content
        assert "Hello, {self.name}!" not in content

    def test_replace_not_found(self, sample_file):
        result = json.loads(replace_function(sample_file, "nonexistent", "def x(): pass"))
        assert "error" in result

    def test_replace_invalid_syntax(self, sample_file):
        result = json.loads(replace_function(sample_file, "simple_function", "def bad(:\n    pass"))
        assert "error" in result
        assert "syntax" in result["error"].lower()

    def test_file_unchanged_on_error(self, sample_file):
        with open(sample_file) as f:
            original = f.read()
        replace_function(sample_file, "simple_function", "def bad(:\n    pass")
        with open(sample_file) as f:
            after = f.read()
        assert original == after

    def test_replace_preserves_other_code(self, sample_file):
        new_src = "def simple_function(x: int, y: int = 0) -> int:\n    return 0\n"
        replace_function(sample_file, "simple_function", new_src)
        with open(sample_file) as f:
            content = f.read()
        assert "class MyClass" in content
        assert "another_function" in content
        assert "async_func" in content


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------


class TestRenameSymbol:
    def test_rename_function(self, sample_file):
        result = json.loads(rename_symbol(sample_file, "simple_function", "add_numbers"))
        assert "error" not in result
        assert result["renamed"] == "simple_function"
        assert result["to"] == "add_numbers"

        with open(sample_file) as f:
            content = f.read()
        assert "def add_numbers" in content
        assert "def simple_function" not in content

    def test_rename_class(self, sample_file):
        result = json.loads(rename_symbol(sample_file, "MyClass", "BetterClass"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "class BetterClass" in content
        assert "class MyClass" not in content

    def test_rename_method(self, sample_file):
        result = json.loads(rename_symbol(sample_file, "MyClass.greet", "say_hello"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "def say_hello" in content
        assert "def greet" not in content

    def test_rename_not_found(self, sample_file):
        result = json.loads(rename_symbol(sample_file, "nonexistent", "new_name"))
        assert "error" in result


# ---------------------------------------------------------------------------
# add_method
# ---------------------------------------------------------------------------


class TestAddMethod:
    def test_add_at_end(self, sample_file):
        new_method = textwrap.dedent("""\
            def new_method(self) -> str:
                return "new"
        """)
        result = json.loads(add_method(sample_file, "MyClass", new_method))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "def new_method" in content

    def test_add_after_method(self, sample_file):
        new_method = textwrap.dedent("""\
            def inserted(self) -> int:
                return 42
        """)
        result = json.loads(add_method(sample_file, "MyClass", new_method, after="__init__"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "def inserted" in content

    def test_add_to_nonexistent_class(self, sample_file):
        result = json.loads(add_method(sample_file, "Nope", "def x(self): pass"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_add_invalid_method(self, sample_file):
        result = json.loads(add_method(sample_file, "MyClass", "def bad(:\n    pass"))
        assert "error" in result


# ---------------------------------------------------------------------------
# remove_symbol
# ---------------------------------------------------------------------------


class TestRemoveSymbol:
    def test_remove_function(self, sample_file):
        result = json.loads(remove_symbol(sample_file, "another_function"))
        assert "error" not in result
        assert result["removed"] == "another_function"

        with open(sample_file) as f:
            content = f.read()
        assert "def another_function" not in content
        assert "def simple_function" in content  # others preserved

    def test_remove_method(self, sample_file):
        result = json.loads(remove_symbol(sample_file, "MyClass._private_method"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "_private_method" not in content
        assert "def greet" in content  # other methods preserved

    def test_remove_class(self, sample_file):
        result = json.loads(remove_symbol(sample_file, "SubClass"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "class SubClass" not in content
        assert "class MyClass" in content

    def test_remove_not_found(self, sample_file):
        result = json.loads(remove_symbol(sample_file, "ghost"))
        assert "error" in result


# ---------------------------------------------------------------------------
# get_imports
# ---------------------------------------------------------------------------


class TestGetImports:
    def test_basic(self, sample_file):
        result = json.loads(get_imports(sample_file))
        assert "imports" in result
        imports = result["imports"]
        assert len(imports) >= 4  # __future__, os, sys, typing

        modules = [i.get("module", "") for i in imports]
        assert "os" in modules
        assert "sys" in modules

    def test_from_import(self, sample_file):
        result = json.loads(get_imports(sample_file))
        typing_imp = [i for i in result["imports"] if i.get("module") == "typing"]
        assert len(typing_imp) == 1
        assert typing_imp[0]["type"] == "from_import"
        names = [n["name"] for n in typing_imp[0]["names"]]
        assert "Optional" in names
        assert "List" in names

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.py"
        p.write_text("")
        result = json.loads(get_imports(str(p)))
        assert result["imports"] == []


# ---------------------------------------------------------------------------
# add_import
# ---------------------------------------------------------------------------


class TestAddImport:
    def test_add_new(self, sample_file):
        result = json.loads(add_import(sample_file, "import json"))
        assert "error" not in result
        assert result["status"] != "already_present" if "status" in result else True

        with open(sample_file) as f:
            content = f.read()
        assert "import json" in content

    def test_already_present(self, sample_file):
        result = json.loads(add_import(sample_file, "import os"))
        assert result.get("status") == "already_present"

    def test_add_from_import(self, sample_file):
        result = json.loads(add_import(sample_file, "from pathlib import Path"))
        assert "error" not in result

        with open(sample_file) as f:
            content = f.read()
        assert "from pathlib import Path" in content

    def test_invalid_import(self, minimal_file):
        result = json.loads(add_import(minimal_file, "from . import ("))
        assert "error" in result


# ---------------------------------------------------------------------------
# Edge cases and error handling
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_decorated_function(self, tmp_path):
        """Functions with decorators should include them in get_source and replace."""
        code = textwrap.dedent("""\
            import functools

            @functools.cache
            @staticmethod
            def cached_fn(x):
                return x * 2
        """)
        p = tmp_path / "decorated.py"
        p.write_text(code)

        result = json.loads(get_source(str(p), "cached_fn"))
        assert "@functools.cache" in result["source"]
        assert "@staticmethod" in result["source"]
        assert "def cached_fn" in result["source"]

    def test_replace_decorated_function(self, tmp_path):
        code = textwrap.dedent("""\
            from typing import override

            @override
            def my_method(self):
                return "old"
        """)
        p = tmp_path / "dec.py"
        p.write_text(code)

        new_src = textwrap.dedent("""\
            @override
            def my_method(self):
                return "new"
        """)
        result = json.loads(replace_function(str(p), "my_method", new_src))
        assert "error" not in result

        with open(str(p)) as f:
            content = f.read()
        assert '"new"' in content
        assert '"old"' not in content

    def test_multiline_signature(self, tmp_path):
        code = textwrap.dedent("""\
            def long_func(
                arg1: str,
                arg2: int,
                arg3: float = 3.14,
            ) -> dict:
                return {"a": arg1}
        """)
        p = tmp_path / "multiline.py"
        p.write_text(code)

        result = json.loads(get_source(str(p), "long_func"))
        assert "def long_func" in result["source"]
        assert "arg3" in result["source"]
        assert "return" in result["source"]

    def test_nested_classes(self, tmp_path):
        code = textwrap.dedent("""\
            class Outer:
                class Inner:
                    def inner_method(self):
                        pass

                def outer_method(self):
                    pass
        """)
        p = tmp_path / "nested.py"
        p.write_text(code)

        result = json.loads(inspect(str(p)))
        outer = next(s for s in result["symbols"] if s["name"] == "Outer")
        member_names = [m["name"] for m in outer["members"]]
        assert "outer_method" in member_names

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.py"
        p.write_text("")
        result = json.loads(inspect(str(p)))
        assert result["symbols"] == []

    def test_varargs_kwargs(self, tmp_path):
        code = textwrap.dedent("""\
            def variadic(*args: str, **kwargs: int) -> None:
                pass
        """)
        p = tmp_path / "varargs.py"
        p.write_text(code)

        result = json.loads(inspect(str(p)))
        func = result["symbols"][0]
        assert "*args" in func["signature"]
        assert "**kwargs" in func["signature"]

    def test_write_permission_error(self, tmp_path):
        """Write failures are surfaced as errors."""
        from unittest.mock import patch
        p = tmp_path / "writable.py"
        p.write_text("def foo():\n    return 1\n")
        with patch("acai.tools.python_code._write_source", return_value="Permission denied"):
            result = json.loads(replace_function(str(p), "foo", "def foo():\n    return 2\n"))
        assert "error" in result
        assert "Permission denied" in result["error"]

    def test_concurrent_operations(self, tmp_path):
        """Multiple sequential edits should all apply cleanly."""
        code = textwrap.dedent("""\
            def a():
                return 1

            def b():
                return 2

            def c():
                return 3
        """)
        p = tmp_path / "multi.py"
        p.write_text(code)
        path = str(p)

        replace_function(path, "a", "def a():\n    return 10\n")
        replace_function(path, "b", "def b():\n    return 20\n")
        replace_function(path, "c", "def c():\n    return 30\n")

        with open(path) as f:
            content = f.read()
        assert "return 10" in content
        assert "return 20" in content
        assert "return 30" in content
