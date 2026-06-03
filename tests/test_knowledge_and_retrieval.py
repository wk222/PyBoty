from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import core.systems.knowledge.embedding_resolver as embedding_resolver
from core.systems.knowledge.embedding_resolver import (
    GeminiEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    VoyageEmbeddingProvider,
    resolve_embeddings,
)
from core.systems.knowledge.knowledge_sources import (
    DirectorySource,
    FileSource,
    GitRepoSource,
    KnowledgeManager,
    TextSource,
    URLSource,
)
from core.systems.knowledge.vector_store import Document, InMemoryVectorStore, SearchResult
from core.systems.knowledge.knowledge_tools import get_knowledge_tools
from core.systems.knowledge.knowledge_retrieval import (
    RetrievalConfig,
    deduplicate_results,
    extract_knowledge_context,
    filter_by_score,
    format_result,
    retrieve_and_format,
)
from core.systems.knowledge.document_pipeline import ChunkConfig, DocumentPipeline, chunk_text


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def store_fixture():
    return InMemoryVectorStore()


@pytest.fixture
def pipeline_fixture(store_fixture):
    return DocumentPipeline(store_fixture, chunk_config=ChunkConfig(chunk_size=100, chunk_overlap=20))


@pytest.fixture
def tools_fixture(store_fixture):
    pipeline = DocumentPipeline(store_fixture)
    return {t.name: t for t in get_knowledge_tools(store_fixture, pipeline)}


# ── Section 1: Knowledge Sources ─────────────────────────────────────

class TestFileSource:
    def test_load_text_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello world, this is a test document.")
            f.flush()
            docs = FileSource(f.name).load()
        os.unlink(f.name)
        assert len(docs) >= 1
        assert "Hello world" in docs[0].page_content
        assert docs[0].metadata["format"] == "text"

    def test_load_nonexistent(self):
        docs = FileSource("/nonexistent/file.txt").load()
        assert docs == []

    def test_load_with_metadata(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\nSome content")
            f.flush()
            docs = FileSource(f.name, metadata={"project": "test"}).load()
        os.unlink(f.name)
        assert docs[0].metadata["project"] == "test"


class TestDirectorySource:
    def test_load_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("File A content", encoding="utf-8")
            (Path(tmpdir) / "b.md").write_text("File B content", encoding="utf-8")
            (Path(tmpdir) / "c.exe").write_text("Binary", encoding="utf-8")
            docs = DirectorySource(tmpdir).load()
            sources = {d.metadata.get("filename") for d in docs}
            assert "a.txt" in sources
            assert "b.md" in sources
            assert "c.exe" not in sources

    def test_nonexistent_directory(self):
        docs = DirectorySource("/nonexistent/dir").load()
        assert docs == []

    def test_custom_extensions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("text", encoding="utf-8")
            (Path(tmpdir) / "b.rs").write_text("fn main() {}", encoding="utf-8")
            docs = DirectorySource(tmpdir, extensions=[".rs"]).load()
            assert all(d.metadata["filename"].endswith(".rs") for d in docs)


class TestTextSource:
    def test_load_text(self):
        docs = TextSource("Some important knowledge to remember").load()
        assert len(docs) >= 1
        assert "important knowledge" in docs[0].page_content

    def test_empty_text(self):
        assert TextSource("").load() == []
        assert TextSource("   ").load() == []

    def test_source_name(self):
        docs = TextSource("content", source_name="user_notes").load()
        assert docs[0].metadata["source"] == "user_notes"


class TestURLSource:
    def test_source_type(self):
        s = URLSource("https://example.com")
        assert s.source_type == "url"

    def test_bad_url(self):
        docs = URLSource("http://this-domain-does-not-exist-12345.com", timeout=2).load()
        assert docs == []


class TestGitRepoSource:
    def test_load_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("print('hello')", encoding="utf-8")
            sub = Path(tmpdir) / "src"
            sub.mkdir()
            (sub / "utils.py").write_text("def helper(): pass", encoding="utf-8")
            (Path(tmpdir) / ".git").mkdir()
            (Path(tmpdir) / ".git" / "config").write_text("git config", encoding="utf-8")

            docs = GitRepoSource(tmpdir).load()
            filenames = {d.metadata.get("filename") for d in docs}
            assert "main.py" in filenames
            assert "utils.py" in filenames
            assert "config" not in filenames  # .git excluded

    def test_nonexistent(self):
        assert GitRepoSource("/nonexistent").load() == []

    def test_exclude_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nm = Path(tmpdir) / "node_modules"
            nm.mkdir()
            (nm / "pkg.js").write_text("module.exports = {}", encoding="utf-8")
            (Path(tmpdir) / "app.js").write_text("console.log('hi')", encoding="utf-8")

            docs = GitRepoSource(tmpdir, extensions=[".js"]).load()
            filenames = {d.metadata.get("filename") for d in docs}
            assert "app.js" in filenames
            assert "pkg.js" not in filenames


class TestKnowledgeManager:
    def setup_method(self):
        self.store = InMemoryVectorStore()
        self.mgr = KnowledgeManager(self.store)

    def test_add_source(self):
        count = self.mgr.add_source(TextSource("Knowledge about Python"))
        assert count >= 1
        assert self.store.count("default") >= 1

    def test_add_multiple_sources(self):
        self.mgr.add_source(TextSource("First piece"), collection="docs")
        self.mgr.add_source(TextSource("Second piece"), collection="docs")
        sources = self.mgr.list_sources()
        assert len(sources) == 2

    def test_refresh(self):
        self.mgr.add_source(TextSource("Version 1"), collection="wiki")
        count1 = self.store.count("wiki")
        total = self.mgr.refresh("wiki")
        assert total == count1

    def test_stats(self):
        self.mgr.add_source(TextSource("A"), collection="c1")
        self.mgr.add_source(TextSource("B"), collection="c1")
        self.mgr.add_source(TextSource("C"), collection="c2")
        stats = self.mgr.stats()
        assert stats["total_sources"] == 3
        assert "c1" in stats["collections"]
        assert stats["collections"]["c1"]["source_count"] == 2

    def test_list_sources(self):
        self.mgr.add_source(TextSource("X"), collection="a")
        sources = self.mgr.list_sources()
        assert len(sources) == 1
        assert sources[0]["source_type"] == "text"
        assert sources[0]["collection"] == "a"

    def test_add_file_source(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("File content for testing knowledge manager")
            f.flush()
            count = self.mgr.add_source(FileSource(f.name))
        os.unlink(f.name)
        assert count >= 1


# ── Section 2: Knowledge Tools ───────────────────────────────────────

class TestKnowledgeSearch:
    def test_search_empty_collection(self, tools_fixture):
        result = tools_fixture["knowledge_search"].invoke({"query": "test"})
        assert "未" in result or "找到" in result

    def test_search_returns_results(self, store_fixture, tools_fixture):
        store_fixture.add_documents(
            [
                Document(page_content="Python is a great programming language"),
                Document(page_content="JavaScript runs in browsers"),
            ]
        )
        result = tools_fixture["knowledge_search"].invoke({"query": "Python programming"})
        assert "Python" in result

    def test_search_custom_collection(self, store_fixture, tools_fixture):
        store_fixture.add_documents([Document(page_content="Private data")], collection="private")
        result = tools_fixture["knowledge_search"].invoke({"query": "data", "collection": "private"})
        assert "Private" in result


class TestKnowledgeIngestText:
    def test_ingest_text(self, tools_fixture, store_fixture):
        result = tools_fixture["knowledge_ingest_text"].invoke({"text": "Important knowledge to remember"})
        assert "成功" in result
        assert store_fixture.count() >= 1

    def test_ingest_empty_text(self, tools_fixture):
        result = tools_fixture["knowledge_ingest_text"].invoke({"text": ""})
        assert "失败" in result


class TestKnowledgeList:
    def test_list_empty(self, tools_fixture):
        result = tools_fixture["knowledge_list"].invoke({})
        assert "空" in result

    def test_list_with_collections(self, store_fixture, tools_fixture):
        store_fixture.add_documents([Document(page_content="a")], collection="docs")
        store_fixture.add_documents([Document(page_content="b")], collection="notes")
        result = tools_fixture["knowledge_list"].invoke({})
        assert "docs" in result
        assert "notes" in result


class TestKnowledgeDelete:
    def test_delete_collection(self, store_fixture, tools_fixture):
        store_fixture.add_documents([Document(page_content="x")], collection="temp")
        result = tools_fixture["knowledge_delete"].invoke({"collection": "temp"})
        assert "删除" in result
        assert store_fixture.count("temp") == 0

    def test_delete_by_ids(self, store_fixture, tools_fixture):
        store_fixture.add_documents(
            [
                Document(page_content="a", doc_id="id1"),
                Document(page_content="b", doc_id="id2"),
            ]
        )
        result = tools_fixture["knowledge_delete"].invoke({"collection": "default", "doc_ids": ["id1"]})
        assert "1" in result
        assert store_fixture.count() == 1

    def test_delete_nonexistent_collection(self, tools_fixture):
        result = tools_fixture["knowledge_delete"].invoke({"collection": "nope"})
        assert "失败" in result


# ── Section 3: Knowledge Retrieval ───────────────────────────────────

def _make_result(content, score, source="file.txt", chunk_index=0, collection="default"):
    return SearchResult(
        document=Document(
            page_content=content,
            metadata={"source": source, "filename": source, "chunk_index": chunk_index},
        ),
        score=score,
        collection=collection,
    )


class TestFilterByScore:
    def test_filters_below_threshold(self):
        results = [_make_result("a", 0.8), _make_result("b", 0.3), _make_result("c", 0.5)]
        filtered = filter_by_score(results, 0.5)
        assert len(filtered) == 2
        assert all(r.score >= 0.5 for r in filtered)

    def test_empty_input(self):
        assert filter_by_score([], 0.5) == []

    def test_all_pass(self):
        results = [_make_result("a", 0.9), _make_result("b", 0.8)]
        assert len(filter_by_score(results, 0.1)) == 2

    def test_none_pass(self):
        results = [_make_result("a", 0.1), _make_result("b", 0.2)]
        assert len(filter_by_score(results, 0.5)) == 0


class TestDeduplicateResults:
    def test_merge_adjacent_chunks(self):
        results = [
            _make_result("chunk 0 content", 0.9, source="doc.txt", chunk_index=0),
            _make_result("chunk 1 content", 0.8, source="doc.txt", chunk_index=1),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
        assert "chunk 0" in deduped[0].document.page_content
        assert "chunk 1" in deduped[0].document.page_content
        assert deduped[0].score == 0.9

    def test_non_adjacent_not_merged(self):
        results = [
            _make_result("chunk 0", 0.9, source="doc.txt", chunk_index=0),
            _make_result("chunk 5", 0.8, source="doc.txt", chunk_index=5),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_different_sources_not_merged(self):
        results = [
            _make_result("a", 0.9, source="a.txt", chunk_index=0),
            _make_result("b", 0.8, source="b.txt", chunk_index=1),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_single_result(self):
        results = [_make_result("only one", 0.9)]
        assert len(deduplicate_results(results)) == 1

    def test_empty(self):
        assert deduplicate_results([]) == []

    def test_three_adjacent_merge(self):
        results = [
            _make_result("A", 0.7, source="x.txt", chunk_index=0),
            _make_result("B", 0.9, source="x.txt", chunk_index=1),
            _make_result("C", 0.6, source="x.txt", chunk_index=2),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
        assert deduped[0].score == 0.9


class TestFormatResult:
    def test_with_metadata(self):
        r = _make_result("Content here", 0.85, source="notes.md")
        text = format_result(r, include_metadata=True)
        assert "notes.md" in text
        assert "0.85" in text
        assert "Content here" in text

    def test_without_metadata(self):
        r = _make_result("Content here", 0.85, source="notes.md")
        text = format_result(r, include_metadata=False)
        assert "notes.md" not in text
        assert "Content here" in text


class TestExtractKnowledgeContext:
    def test_basic(self):
        results = [_make_result("Fact A", 0.9), _make_result("Fact B", 0.7)]
        ctx = extract_knowledge_context(results)
        assert "相关知识" in ctx
        assert "Fact A" in ctx
        assert "Fact B" in ctx

    def test_empty(self):
        assert extract_knowledge_context([]) == ""

    def test_custom_template(self):
        results = [_make_result("data", 0.9)]
        ctx = extract_knowledge_context(results, template="Context: {snippets}")
        assert ctx.startswith("Context:")


class TestRetrieveAndFormat:
    def setup_method(self):
        self.store = InMemoryVectorStore()
        for i, text in enumerate(
            [
                "Python is a programming language",
                "Python supports object-oriented programming",
                "JavaScript runs in the browser",
                "Rust is a systems programming language",
            ]
        ):
            self.store.add_documents(
                [
                    Document(
                        page_content=text,
                        metadata={
                            "source": f"doc{i}.txt",
                            "filename": f"doc{i}.txt",
                            "chunk_index": 0,
                        },
                    )
                ],
                collection="test",
            )

    def test_basic_retrieval(self):
        result = retrieve_and_format(self.store, "Python programming", collection="test")
        assert "Python" in result

    def test_score_threshold(self):
        config = RetrievalConfig(score_threshold=0.99)
        result = retrieve_and_format(self.store, "xyzzy_nonexistent_term", collection="test", config=config)
        assert result == ""

    def test_max_results(self):
        config = RetrievalConfig(max_results=1, score_threshold=0.0)
        result = retrieve_and_format(self.store, "programming", collection="test", config=config)
        assert result.count("---") <= 1


# ── Section 4: Document Pipeline & Chunking ──────────────────────────

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
    def test_ingest_text_file(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("This is a test document.\n\nIt has multiple paragraphs.\n\nThird paragraph here.")
            f.flush()
            path = f.name

        try:
            result = pipeline_fixture.ingest(path)
            assert result.chunk_count >= 1
            assert result.format == "text"
            assert not result.errors
            assert store_fixture.count() >= 1
        finally:
            os.unlink(path)

    def test_ingest_markdown_file(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Title\n\nSome content here.\n\n## Section\n\nMore content.")
            f.flush()
            path = f.name

        try:
            result = pipeline_fixture.ingest(path)
            assert result.format == "markdown"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_json_file(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write('[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]')
            f.flush()
            path = f.name

        try:
            result = pipeline_fixture.ingest(path)
            assert result.format == "json"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_csv_file(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="") as f:
            f.write("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
            f.flush()
            path = f.name

        try:
            result = pipeline_fixture.ingest(path)
            assert result.format == "csv"
            assert result.chunk_count >= 1
        finally:
            os.unlink(path)

    def test_ingest_nonexistent_file(self, pipeline_fixture):
        result = pipeline_fixture.ingest("/nonexistent/file.txt")
        assert result.chunk_count == 0
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_ingest_text_direct(self, pipeline_fixture, store_fixture):
        result = pipeline_fixture.ingest_text("Some important knowledge to remember.")
        assert result.chunk_count >= 1
        assert store_fixture.count() >= 1

    def test_ingest_empty_text(self, pipeline_fixture):
        result = pipeline_fixture.ingest_text("")
        assert result.chunk_count == 0
        assert "Empty" in result.errors[0]

    def test_ingest_to_custom_collection(self, pipeline_fixture, store_fixture):
        result = pipeline_fixture.ingest_text("Hello", collection="custom")
        assert result.collection == "custom"
        assert store_fixture.count("custom") >= 1

    def test_ingest_preserves_metadata(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Content for metadata test")
            f.flush()
            path = f.name

        try:
            pipeline_fixture.ingest(path)
            results = store_fixture.search("metadata test")
            assert results[0].document.metadata["format"] == "text"
            assert "filename" in results[0].document.metadata
        finally:
            os.unlink(path)

    def test_ingest_html_strips_tags(self, pipeline_fixture, store_fixture):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write("<html><body><h1>Title</h1><p>Content here</p><script>evil()</script></body></html>")
            f.flush()
            path = f.name

        try:
            result = pipeline_fixture.ingest(path)
            assert result.format == "html"
            results = store_fixture.search("Content")
            assert "script" not in results[0].document.page_content.lower()
        finally:
            os.unlink(path)


class TestIngestDirectory:
    def test_ingest_directory(self, pipeline_fixture, store_fixture):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                with open(os.path.join(tmpdir, f"doc{i}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"Document {i} content")

            results = pipeline_fixture.ingest_directory(tmpdir)
            assert len(results) == 3
            assert store_fixture.count() >= 3


# ── Section 5: Embedding Resolver ────────────────────────────────────

class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None, params=None):  # noqa: ANN001,ARG002
        return _DummyResponse(self.payload)


def test_resolve_embeddings_supports_new_remote_providers():
    assert isinstance(resolve_embeddings("ollama:nomic-embed-text"), OllamaEmbeddingProvider)
    assert isinstance(resolve_embeddings("voyage:voyage-3-lite", api_key="k"), VoyageEmbeddingProvider)
    assert isinstance(resolve_embeddings("gemini:text-embedding-004", api_key="k"), GeminiEmbeddingProvider)


def test_openai_embedding_provider_batches_remote_requests(monkeypatch):
    provider = resolve_embeddings(
        {"provider": "openai", "model": "text-embedding-3-small", "api_key": "key", "batch_size": 2}
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)

    monkeypatch.setattr(
        embedding_resolver.httpx,
        "Client",
        lambda timeout=20.0: _DummyClient({"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]}),
    )

    vectors = provider.embed_documents(["alpha", "beta"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_gemini_embedding_provider_reads_values_payload(monkeypatch):
    provider = resolve_embeddings(
        {"provider": "gemini", "model": "text-embedding-004", "api_key": "key", "batch_size": 2}
    )
    assert isinstance(provider, GeminiEmbeddingProvider)

    monkeypatch.setattr(
        embedding_resolver.httpx,
        "Client",
        lambda timeout=20.0: _DummyClient({"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}),
    )

    vectors = provider.embed_documents(["one", "two"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
