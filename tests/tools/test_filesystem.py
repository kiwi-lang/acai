"""Tests for acai.tools.filesystem — file read/write/edit/list operations."""

from __future__ import annotations

import json
import os

import pytest

from acai.tools.filesystem import (
    read_file,
    edit_file,
    write_file,
    list_directory,
    make_directory,
    delete_file,
    file_info,
)


@pytest.fixture
def sample_file(tmp_path):
    """Create a sample multi-line file."""
    f = tmp_path / "sample.txt"
    lines = [f"Line {i}\n" for i in range(1, 21)]
    f.write_text("".join(lines))
    return str(f)


class TestReadFile:

    def test_reads_full_file(self, sample_file):
        content = read_file(sample_file)
        assert "Line 1\n" in content
        assert "Line 20\n" in content

    def test_line_start_and_limit(self, sample_file):
        content = read_file(sample_file, line_start=5, line_limit=3)
        assert content == "Line 5\nLine 6\nLine 7\n"

    def test_offset_alias(self, sample_file):
        content = read_file(sample_file, offset=10, limit=2)
        assert content == "Line 10\nLine 11\n"

    def test_truncation_at_max_lines(self, sample_file):
        content = read_file(sample_file, max_lines=5)
        assert "truncated" in content
        assert "Line 5\n" in content
        assert "Line 6\n" not in content

    def test_no_truncation_when_disabled(self, sample_file):
        content = read_file(sample_file, max_lines=0)
        assert "truncated" not in content
        assert "Line 20\n" in content

    def test_nonexistent_file_returns_error(self):
        result = read_file("/nonexistent/path/file.txt")
        data = json.loads(result)
        assert "error" in data


class TestEditFile:

    def test_replace_one_occurrence(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world hello world")
        result = json.loads(edit_file(str(f), "hello", "goodbye"))
        assert result["ok"] is True
        assert result["replacements"] == 1
        assert f.read_text() == "goodbye world hello world"

    def test_replace_all_occurrences(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("aaa bbb aaa")
        result = json.loads(edit_file(str(f), "aaa", "ccc", replace_all=True))
        assert result["replacements"] == 2
        assert f.read_text() == "ccc bbb ccc"

    def test_old_string_not_found(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("foo bar")
        result = json.loads(edit_file(str(f), "xyz", "abc"))
        assert "error" in result
        assert "not found" in result["error"]


class TestWriteFile:

    def test_creates_file_and_parents(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "file.txt")
        result = json.loads(write_file(path, "hello"))
        assert result["written"] == path
        assert open(path).read() == "hello"

    def test_overwrites_existing(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("old")
        result = json.loads(write_file(str(f), "new"))
        assert result["written"] == str(f)
        assert f.read_text() == "new"


class TestListDirectory:

    def test_list_flat(self, tmp_path):
        (tmp_path / "alpha.txt").touch()
        (tmp_path / "beta").mkdir()
        result = json.loads(list_directory(str(tmp_path)))
        names = [e["name"] for e in result]
        assert "alpha.txt" in names
        assert "beta" in names
        dirs = [e for e in result if e["name"] == "beta"]
        assert dirs[0]["type"] == "dir"

    def test_list_recursive(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.py").touch()
        (tmp_path / "top.txt").touch()
        result = json.loads(list_directory(str(tmp_path), recursive=True))
        assert "top.txt" in result
        assert os.path.join("sub", "file.py") in result

    def test_nonexistent_dir(self):
        result = json.loads(list_directory("/nonexistent_dir_xyz"))
        assert "error" in result


class TestMakeDirectory:

    def test_creates_nested(self, tmp_path):
        path = str(tmp_path / "x" / "y" / "z")
        result = json.loads(make_directory(path))
        assert result["created"] == path
        assert os.path.isdir(path)

    def test_existing_dir_no_error(self, tmp_path):
        result = json.loads(make_directory(str(tmp_path)))
        assert result["created"] == str(tmp_path)


class TestDeleteFile:

    def test_deletes_existing(self, tmp_path):
        f = tmp_path / "to_delete.txt"
        f.write_text("bye")
        result = json.loads(delete_file(str(f)))
        assert result["deleted"] == str(f)
        assert not f.exists()

    def test_nonexistent_file(self):
        result = json.loads(delete_file("/nonexistent/file.xyz"))
        assert "error" in result


class TestFileInfo:

    def test_returns_info(self, tmp_path):
        f = tmp_path / "info.txt"
        f.write_text("test content")
        result = json.loads(file_info(str(f)))
        assert result["path"] == str(f)
        assert result["size"] == 12
        assert result["is_dir"] is False

    def test_directory(self, tmp_path):
        result = json.loads(file_info(str(tmp_path)))
        assert result["is_dir"] is True

    def test_nonexistent(self):
        result = json.loads(file_info("/does_not_exist_xyz"))
        assert "error" in result
