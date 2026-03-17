"""Document ingestion pipeline for RAG.

Handles: file reading → text extraction → chunking → vector storage.
Supported formats: .txt, .md, .py, .json, .csv, .html, .pdf (with optional deps).
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .vector_store import Document, VectorStoreBackend

logger = logging.getLogger(__name__)


@dataclass
class ChunkConfig:
    """Configuration for text chunking."""

    chunk_size: int = 1000
    chunk_overlap: int = 200
    separator: str = "\n\n"


@dataclass
class IngestResult:
    """Result of a document ingestion operation."""

    path: str
    collection: str
    chunk_count: int
    doc_ids: list[str]
    format: str
    errors: list[str] = field(default_factory=list)


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_json_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return "\n\n".join(json.dumps(item, ensure_ascii=False, indent=2) for item in data)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _read_csv_file(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return ""
    header = rows[0]
    lines = []
    for row in rows[1:]:
        entries = [f"{h}: {v}" for h, v in zip(header, row, strict=False) if v.strip()]
        lines.append("; ".join(entries))
    return "\n\n".join(lines)


def _read_html_file(path: str) -> str:
    """Extract text from HTML, stripping tags."""
    import re

    raw = _read_text_file(path)
    text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _detect_format(path: str) -> str:
    ext = Path(path).suffix.lower()
    format_map = {
        ".txt": "text",
        ".md": "markdown",
        ".py": "code",
        ".js": "code",
        ".ts": "code",
        ".java": "code",
        ".go": "code",
        ".rs": "code",
        ".c": "code",
        ".cpp": "code",
        ".h": "code",
        ".json": "json",
        ".csv": "csv",
        ".html": "html",
        ".htm": "html",
        ".pdf": "pdf",
        ".yaml": "text",
        ".yml": "text",
        ".toml": "text",
        ".xml": "text",
        ".log": "text",
    }
    return format_map.get(ext, "text")


def _extract_text(path: str, fmt: str) -> str:
    """Extract text content from a file based on format."""
    if fmt == "json":
        return _read_json_file(path)
    if fmt == "csv":
        return _read_csv_file(path)
    if fmt == "html":
        return _read_html_file(path)
    if fmt == "pdf":
        return _read_pdf_file(path)
    return _read_text_file(path)


def _read_pdf_file(path: str) -> str:
    """Extract text from PDF. Requires pypdf or pdfplumber."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except ImportError:
        pass

    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n\n".join(pages)
    except ImportError:
        raise ImportError("PDF support requires pypdf or pdfplumber. Install with: pip install pypdf") from None


def chunk_text(text: str, config: ChunkConfig | None = None) -> list[str]:
    """Split text into overlapping chunks."""
    cfg = config or ChunkConfig()
    if len(text) <= cfg.chunk_size:
        return [text] if text.strip() else []

    paragraphs = text.split(cfg.separator)
    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        if not para.strip():
            continue
        if len(current_chunk) + len(para) + len(cfg.separator) > cfg.chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            overlap_text = current_chunk[-cfg.chunk_overlap :] if cfg.chunk_overlap > 0 else ""
            current_chunk = overlap_text + cfg.separator + para
        else:
            current_chunk = (current_chunk + cfg.separator + para) if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    if not chunks and text.strip():
        for i in range(0, len(text), cfg.chunk_size - cfg.chunk_overlap):
            chunk = text[i : i + cfg.chunk_size]
            if chunk.strip():
                chunks.append(chunk.strip())

    return chunks


class DocumentPipeline:
    """End-to-end document ingestion: file → chunks → vector store."""

    def __init__(
        self,
        vector_store: VectorStoreBackend,
        chunk_config: ChunkConfig | None = None,
    ):
        self.vector_store = vector_store
        self.chunk_config = chunk_config or ChunkConfig()

    def ingest(self, path: str, collection: str = "default", metadata: dict[str, Any] | None = None) -> IngestResult:
        """Ingest a file into the vector store."""
        if not os.path.isfile(path):
            return IngestResult(
                path=path,
                collection=collection,
                chunk_count=0,
                doc_ids=[],
                format="unknown",
                errors=[f"File not found: {path}"],
            )

        fmt = _detect_format(path)
        errors: list[str] = []

        try:
            text = _extract_text(path, fmt)
        except Exception as exc:
            return IngestResult(
                path=path,
                collection=collection,
                chunk_count=0,
                doc_ids=[],
                format=fmt,
                errors=[f"Extraction error: {exc}"],
            )

        if not text.strip():
            return IngestResult(
                path=path,
                collection=collection,
                chunk_count=0,
                doc_ids=[],
                format=fmt,
                errors=["Empty content"],
            )

        chunks = chunk_text(text, self.chunk_config)
        file_name = os.path.basename(path)
        base_metadata = {"source": path, "filename": file_name, "format": fmt}
        if metadata:
            base_metadata.update(metadata)

        docs = []
        for i, chunk in enumerate(chunks):
            doc_meta = {**base_metadata, "chunk_index": i, "total_chunks": len(chunks)}
            docs.append(Document(page_content=chunk, metadata=doc_meta))

        doc_ids = self.vector_store.add_documents(docs, collection=collection)
        logger.info("Ingested %s: %d chunks into %r", file_name, len(chunks), collection)
        return IngestResult(
            path=path,
            collection=collection,
            chunk_count=len(chunks),
            doc_ids=doc_ids,
            format=fmt,
            errors=errors,
        )

    def ingest_text(
        self,
        text: str,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
        source: str = "direct_input",
    ) -> IngestResult:
        """Ingest raw text directly."""
        if not text.strip():
            return IngestResult(
                path=source,
                collection=collection,
                chunk_count=0,
                doc_ids=[],
                format="text",
                errors=["Empty content"],
            )

        chunks = chunk_text(text, self.chunk_config)
        base_metadata = {"source": source, "format": "text"}
        if metadata:
            base_metadata.update(metadata)

        docs = []
        for i, chunk in enumerate(chunks):
            doc_meta = {**base_metadata, "chunk_index": i, "total_chunks": len(chunks)}
            docs.append(Document(page_content=chunk, metadata=doc_meta))

        doc_ids = self.vector_store.add_documents(docs, collection=collection)
        return IngestResult(
            path=source,
            collection=collection,
            chunk_count=len(chunks),
            doc_ids=doc_ids,
            format="text",
        )

    def ingest_directory(
        self,
        directory: str,
        collection: str = "default",
        extensions: list[str] | None = None,
        recursive: bool = True,
    ) -> list[IngestResult]:
        """Ingest all matching files from a directory."""
        allowed_ext = set(extensions or [".txt", ".md", ".py", ".json", ".csv", ".html"])
        results: list[IngestResult] = []

        walk_fn = os.walk if recursive else lambda d: [(d, [], os.listdir(d))]
        for root, _dirs, files in walk_fn(directory):
            for fname in sorted(files):
                if Path(fname).suffix.lower() in allowed_ext:
                    fpath = os.path.join(root, fname)
                    result = self.ingest(fpath, collection=collection)
                    results.append(result)

        return results
