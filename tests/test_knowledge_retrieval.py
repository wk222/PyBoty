"""Tests for core.knowledge_retrieval."""

from __future__ import annotations

from core.knowledge_retrieval import (
    RetrievalConfig,
    deduplicate_results,
    extract_knowledge_context,
    filter_by_score,
    format_result,
    retrieve_and_format,
)
from core.vector_store import Document, InMemoryVectorStore, SearchResult


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
        assert result == ""  # no results above threshold

    def test_max_results(self):
        config = RetrievalConfig(max_results=1, score_threshold=0.0)
        result = retrieve_and_format(self.store, "programming", collection="test", config=config)
        assert result.count("---") <= 1  # at most 1 separator
