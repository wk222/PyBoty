"""Pluggable knowledge sources for the RAG pipeline.

Each source knows how to produce Document objects from its backing store
(file, directory, URL, text, git repo). KnowledgeManager coordinates
multiple sources into a single vector store.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .document_pipeline import _detect_format, _extract_text, chunk_text
from .vector_store import Document, VectorStoreBackend

logger = logging.getLogger(__name__)


@runtime_checkable
class KnowledgeSource(Protocol):
    """Protocol for knowledge data sources."""

    @property
    def source_type(self) -> str: ...

    def load(self) -> list[Document]: ...


class FileSource:
    """Single file knowledge source."""

    def __init__(self, path: str, *, metadata: dict[str, Any] | None = None):
        self.path = path
        self._metadata = metadata or {}

    @property
    def source_type(self) -> str:
        return "file"

    def load(self) -> list[Document]:
        if not os.path.isfile(self.path):
            logger.warning("FileSource: file not found: %s", self.path)
            return []
        fmt = _detect_format(self.path)
        try:
            text = _extract_text(self.path, fmt)
        except Exception as exc:
            logger.warning("FileSource: extraction failed for %s: %s", self.path, exc)
            return []
        if not text.strip():
            return []
        chunks = chunk_text(text)
        fname = os.path.basename(self.path)
        docs = []
        for i, chunk in enumerate(chunks):
            meta = {
                "source": self.path,
                "filename": fname,
                "format": fmt,
                "chunk_index": i,
                "total_chunks": len(chunks),
                **self._metadata,
            }
            docs.append(Document(page_content=chunk, metadata=meta))
        return docs


class DirectorySource:
    """Directory of files as knowledge source."""

    def __init__(
        self,
        path: str,
        *,
        extensions: list[str] | None = None,
        recursive: bool = True,
    ):
        self.path = path
        self.extensions = set(extensions or [".txt", ".md", ".py", ".json", ".csv", ".html"])
        self.recursive = recursive

    @property
    def source_type(self) -> str:
        return "directory"

    def load(self) -> list[Document]:
        if not os.path.isdir(self.path):
            logger.warning("DirectorySource: not a directory: %s", self.path)
            return []
        all_docs: list[Document] = []
        walk = os.walk if self.recursive else lambda d: [(d, [], os.listdir(d))]
        for root, _dirs, files in walk(self.path):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in self.extensions:
                    fpath = os.path.join(root, fname)
                    fs = FileSource(fpath)
                    all_docs.extend(fs.load())
        return all_docs


class TextSource:
    """Raw text as knowledge source."""

    def __init__(self, text: str, *, source_name: str = "text_input"):
        self.text = text
        self.source_name = source_name

    @property
    def source_type(self) -> str:
        return "text"

    def load(self) -> list[Document]:
        if not self.text.strip():
            return []
        chunks = chunk_text(self.text)
        docs = []
        for i, chunk in enumerate(chunks):
            meta = {
                "source": self.source_name,
                "format": "text",
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            docs.append(Document(page_content=chunk, metadata=meta))
        return docs


class URLSource:
    """Web URL as knowledge source (fetches and extracts text)."""

    def __init__(self, url: str, *, timeout: int = 30):
        self.url = url
        self.timeout = timeout

    @property
    def source_type(self) -> str:
        return "url"

    def load(self) -> list[Document]:
        try:
            import re
            import urllib.request

            req = urllib.request.Request(self.url, headers={"User-Agent": "PyBot/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            if not text:
                return []

            chunks = chunk_text(text)
            docs = []
            for i, chunk in enumerate(chunks):
                meta = {
                    "source": self.url,
                    "format": "url",
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                }
                docs.append(Document(page_content=chunk, metadata=meta))
            return docs
        except Exception as exc:
            logger.warning("URLSource: failed to fetch %s: %s", self.url, exc)
            return []


class GitRepoSource:
    """Git repository source code files as knowledge."""

    def __init__(
        self,
        repo_path: str,
        *,
        extensions: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
    ):
        self.repo_path = repo_path
        self.extensions = set(extensions or [".py", ".js", ".ts", ".java", ".go", ".rs", ".md", ".txt"])
        self.exclude_dirs = set(
            exclude_dirs
            or [
                ".git",
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
                "dist",
                "build",
                ".tox",
                ".mypy_cache",
            ]
        )

    @property
    def source_type(self) -> str:
        return "git_repo"

    def load(self) -> list[Document]:
        if not os.path.isdir(self.repo_path):
            logger.warning("GitRepoSource: not a directory: %s", self.repo_path)
            return []
        all_docs: list[Document] = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            for fname in sorted(files):
                if Path(fname).suffix.lower() in self.extensions:
                    fpath = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, self.repo_path)
                    fs = FileSource(fpath, metadata={"repo_path": rel_path})
                    all_docs.extend(fs.load())
        return all_docs


@dataclass
class _SourceRecord:
    source: KnowledgeSource
    collection: str
    added_at: float = field(default_factory=time.time)
    doc_count: int = 0


class KnowledgeManager:
    """Manages multiple knowledge sources into a unified vector store."""

    def __init__(self, vector_store: VectorStoreBackend):
        self._vector_store = vector_store
        self._sources: list[_SourceRecord] = []

    def add_source(
        self,
        source: KnowledgeSource,
        collection: str = "default",
    ) -> int:
        """Add a source, ingest its documents, return doc count."""
        docs = source.load()
        if docs:
            self._vector_store.add_documents(docs, collection=collection)
        record = _SourceRecord(source=source, collection=collection, doc_count=len(docs))
        self._sources.append(record)
        logger.info(
            "KnowledgeManager: added %s source (%d docs) to %r",
            source.source_type,
            len(docs),
            collection,
        )
        return len(docs)

    def refresh(self, collection: str = "default") -> int:
        """Re-ingest all sources for a collection. Returns total doc count."""
        self._vector_store.delete_collection(collection)
        total = 0
        for record in self._sources:
            if record.collection != collection:
                continue
            docs = record.source.load()
            if docs:
                self._vector_store.add_documents(docs, collection=collection)
            record.doc_count = len(docs)
            record.added_at = time.time()
            total += len(docs)
        return total

    def list_sources(self) -> list[dict[str, Any]]:
        return [
            {
                "source_type": r.source.source_type,
                "collection": r.collection,
                "doc_count": r.doc_count,
                "added_at": r.added_at,
            }
            for r in self._sources
        ]

    def stats(self) -> dict[str, Any]:
        collections: dict[str, dict[str, Any]] = {}
        for r in self._sources:
            if r.collection not in collections:
                collections[r.collection] = {
                    "source_count": 0,
                    "doc_count": self._vector_store.count(r.collection),
                    "last_updated": 0.0,
                }
            collections[r.collection]["source_count"] += 1
            collections[r.collection]["last_updated"] = max(collections[r.collection]["last_updated"], r.added_at)
        return {"collections": collections, "total_sources": len(self._sources)}
