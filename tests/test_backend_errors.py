"""Tests for FileOperationError / WriteResult / EditResult in backend_protocol."""

from __future__ import annotations

import pytest

from core.backend_protocol import (
    CompositeBackend,
    EditResult,
    LocalFilesystemBackend,
    WriteResult,
)


@pytest.fixture()
def backend(tmp_path):
    return LocalFilesystemBackend(root_dir=str(tmp_path))


class TestWriteResult:
    def test_write_success(self, backend, tmp_path):
        result = backend.write("hello.txt", "world")
        assert isinstance(result, WriteResult)
        assert result.error is None
        assert result.path is not None
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "world"

    def test_write_creates_subdirs(self, backend, tmp_path):
        result = backend.write("sub/dir/file.txt", "nested")
        assert result.error is None
        assert (tmp_path / "sub" / "dir" / "file.txt").exists()

    def test_write_permission_denied_on_traversal(self, backend):
        result = backend.write("../../escape.txt", "bad")
        assert result.error == "permission_denied"

    def test_write_overwrites_existing(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("old", encoding="utf-8")
        result = backend.write("f.txt", "new")
        assert result.error is None
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new"


class TestEditResult:
    def test_edit_success(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("hello world", encoding="utf-8")
        result = backend.edit("f.txt", "hello", "goodbye")
        assert isinstance(result, EditResult)
        assert result.error is None
        assert result.old_found is True
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "goodbye world"

    def test_edit_file_not_found(self, backend):
        result = backend.edit("nope.txt", "a", "b")
        assert result.error == "file_not_found"
        assert result.old_found is False

    def test_edit_is_directory(self, backend, tmp_path):
        (tmp_path / "adir").mkdir()
        result = backend.edit("adir", "a", "b")
        assert result.error == "is_directory"

    def test_edit_old_not_found(self, backend, tmp_path):
        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        result = backend.edit("f.txt", "missing", "replacement")
        assert result.error is None
        assert result.old_found is False

    def test_edit_permission_denied_on_traversal(self, backend):
        result = backend.edit("../../escape.txt", "a", "b")
        assert result.error == "permission_denied"


class TestCompositeBackendStructuredResults:
    def test_composite_write_returns_write_result(self, tmp_path):
        backend = LocalFilesystemBackend(root_dir=str(tmp_path))
        composite = CompositeBackend(default=backend)
        result = composite.write("test.txt", "hello")
        assert isinstance(result, WriteResult)
        assert result.error is None

    def test_composite_edit_returns_edit_result(self, tmp_path):
        backend = LocalFilesystemBackend(root_dir=str(tmp_path))
        (tmp_path / "test.txt").write_text("abc", encoding="utf-8")
        composite = CompositeBackend(default=backend)
        result = composite.edit("test.txt", "abc", "xyz")
        assert isinstance(result, EditResult)
        assert result.old_found is True
