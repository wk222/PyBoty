"""Tests for BackendFactory pattern and resolve_backend."""

from __future__ import annotations

import tempfile

from core.systems.runtime.backend_protocol import (
    BackendFactory,
    LocalFilesystemBackend,
    resolve_backend,
)


class TestResolveBackend:
    def test_instance_returned_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalFilesystemBackend(root_dir=tmp)
            resolved = resolve_backend(backend)
            assert resolved is backend

    def test_factory_called_with_args(self):
        with tempfile.TemporaryDirectory() as tmp:

            def factory(root: str) -> LocalFilesystemBackend:
                return LocalFilesystemBackend(root_dir=root)

            resolved = resolve_backend(factory, tmp)
            assert isinstance(resolved, LocalFilesystemBackend)
            assert resolved.root_dir == tmp

    def test_factory_called_with_kwargs(self):
        with tempfile.TemporaryDirectory() as tmp:

            def factory(*, root_dir: str) -> LocalFilesystemBackend:
                return LocalFilesystemBackend(root_dir=root_dir)

            resolved = resolve_backend(factory, root_dir=tmp)
            assert isinstance(resolved, LocalFilesystemBackend)

    def test_backend_factory_type_is_callable(self):
        assert callable(BackendFactory)
