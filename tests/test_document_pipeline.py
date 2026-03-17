"""Tests for core.document_pipeline — document ingestion and chunking."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.document_pipeline import ChunkConfig, DocumentPipeline, chunk_text
from core.vector_store import InMemoryVectorStore


@pytest.fixture
def store():
    return InMemoryVectorStore()


@pytest.fixture
def pipeline(store):
    return DocumentPipeline(store, chunk_config=ChunkConfig(chunk_size=100, chunk_overlap=20))


class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("Hello world", ChunkConfig(chunk_size=100))
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_empty_text(self):
        chunks = chunk_text("", ChunkConfig(chunk_size=100))
        assert chunks == []

    def test_whitespace_only(self):
        chunks = chunk_text("   \n\n  ", ChunkConfig(chunk_size=100))
        assert chunks == []

    def test_multi_paragraph_chunking(self):
        text = "\n\n".join([f"Paragraph {i} with some content here." for i in range(10)])
        chunks = chunk_text(text, ChunkConfig(chunk_size=100, chunk_overlap=20))
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) > 0

    def test_overlap_present(self):
        text = "First paragraph with content.\n\nSecond paragraph with different content.\n\nThird paragraph final."
        chunks = chunk_text(text, ChunkConfig(chunk_size=60, chunk_overlap=20))
        assert len(chunks) >= 2


class TestDocumentPipeline:
    def test_ingest_text_file(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("This is a test document.\n\nIt has multiple paragraphs.\n\nThird paragraph here.")
            f.flush()
            path = f.name

        try:
            result = pipeline.ingest(path)
            assert result.chunk_count >= 1
            assert result.format == "text"
            assert not result.errors
            assert store.count() >= 1
        finally:
            os.unlink(path)

    def test_ingest_markdown_file(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\n\nSome content here.\n\n## Section\n\nMore content.")
            f.flush()
            path = f.name

        try:
            result = pipeline.ingest(path)
            assert result.format == "markdown"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_json_file(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write('[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]')
            f.flush()
            path = f.name

        try:
            result = pipeline.ingest(path)
            assert result.format == "json"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_csv_file(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="") as f:
            f.write("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
            f.flush()
            path = f.name

        try:
            result = pipeline.ingest(path)
            assert result.format == "csv"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_nonexistent_file(self, pipeline):
        result = pipeline.ingest("/nonexistent/file.txt")
        assert result.chunk_count == 0
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_ingest_text_direct(self, pipeline, store):
        result = pipeline.ingest_text("Some important knowledge to remember.")
        assert result.chunk_count >= 1
        assert store.count() >= 1

    def test_ingest_empty_text(self, pipeline):
        result = pipeline.ingest_text("")
        assert result.chunk_count == 0
        assert "Empty" in result.errors[0]

    def test_ingest_to_custom_collection(self, pipeline, store):
        result = pipeline.ingest_text("Hello", collection="custom")
        assert result.collection == "custom"
        assert store.count("custom") >= 1

    def test_ingest_preserves_metadata(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Content for metadata test")
            f.flush()
            path = f.name

        try:
            pipeline.ingest(path)
            results = store.search("metadata test")
            assert results[0].document.metadata["format"] == "text"
            assert "filename" in results[0].document.metadata
        finally:
            os.unlink(path)

    def test_ingest_html_strips_tags(self, pipeline, store):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write("<html><body><h1>Title</h1><p>Content here</p><script>evil()</script></body></html>")
            f.flush()
            path = f.name

        try:
            result = pipeline.ingest(path)
            assert result.format == "html"
            results = store.search("Content")
            assert "script" not in results[0].document.page_content.lower()
        finally:
            os.unlink(path)


class TestIngestDirectory:
    def test_ingest_directory(self, pipeline, store):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                with open(os.path.join(tmpdir, f"doc{i}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"Document {i} content")

            results = pipeline.ingest_directory(tmpdir)
            assert len(results) == 3
            assert store.count() >= 3
