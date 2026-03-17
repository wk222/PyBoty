"""Retrieval post-processing for knowledge search results.

Adds score filtering, deduplication of adjacent chunks from the same
source, and configurable output formatting for prompt injection.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vector_store import SearchResult, VectorStoreBackend

_DEFAULT_TEMPLATE = "相关知识:\n{snippets}"


@dataclass
class RetrievalConfig:
    score_threshold: float = 0.1
    max_results: int = 5
    include_metadata: bool = True
    context_template: str = _DEFAULT_TEMPLATE
    merge_adjacent_chunks: bool = True


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    """Merge adjacent chunks from the same source file.

    If two results come from the same source and have consecutive
    chunk_index values, combine them into one with the higher score.
    """
    if len(results) <= 1:
        return results

    grouped: dict[str, list[SearchResult]] = {}
    for r in results:
        source = r.document.metadata.get("source", "")
        grouped.setdefault(source, []).append(r)

    deduped: list[SearchResult] = []
    for _source, group in grouped.items():
        group.sort(key=lambda r: r.document.metadata.get("chunk_index", 0))
        merged: list[SearchResult] = [group[0]]

        for r in group[1:]:
            prev = merged[-1]
            prev_meta = prev.document.metadata
            prev_end = prev_meta.get("chunk_index", -999) + prev_meta.get("merged_chunks", 1) - 1
            cur_idx = r.document.metadata.get("chunk_index", -999)
            if cur_idx == prev_end + 1:
                from .vector_store import Document

                combined_content = prev.document.page_content + "\n\n" + r.document.page_content
                start_idx = prev_meta.get("chunk_index", 0)
                combined_meta = {
                    **prev.document.metadata,
                    "chunk_index": start_idx,
                    "merged_chunks": cur_idx - start_idx + 1,
                }
                merged[-1] = SearchResult(
                    document=Document(page_content=combined_content, metadata=combined_meta),
                    score=max(prev.score, r.score),
                    collection=prev.collection,
                )
            else:
                merged.append(r)
        deduped.extend(merged)

    deduped.sort(key=lambda r: r.score, reverse=True)
    return deduped


def filter_by_score(results: list[SearchResult], threshold: float) -> list[SearchResult]:
    return [r for r in results if r.score >= threshold]


def format_result(result: SearchResult, *, include_metadata: bool = True) -> str:
    """Format a single search result for prompt injection."""
    lines = []
    if include_metadata:
        meta = result.document.metadata
        source = meta.get("filename", meta.get("source", "unknown"))
        score_str = f"{result.score:.2f}"
        lines.append(f"[来源: {source} | 相关度: {score_str}]")
    lines.append(result.document.page_content)
    return "\n".join(lines)


def extract_knowledge_context(
    results: list[SearchResult],
    template: str = _DEFAULT_TEMPLATE,
    *,
    include_metadata: bool = True,
) -> str:
    """Format search results into a context string for prompt injection."""
    if not results:
        return ""
    snippets = "\n\n---\n\n".join(format_result(r, include_metadata=include_metadata) for r in results)
    return template.replace("{snippets}", snippets)


def retrieve_and_format(
    vector_store: VectorStoreBackend,
    query: str,
    *,
    collection: str = "default",
    config: RetrievalConfig | None = None,
) -> str:
    """Full retrieval pipeline: search → filter → dedupe → format."""
    cfg = config or RetrievalConfig()
    fetch_k = max(cfg.max_results * 3, 15)
    raw_results = vector_store.search(query, collection=collection, top_k=fetch_k)

    filtered = filter_by_score(raw_results, cfg.score_threshold)

    if cfg.merge_adjacent_chunks:
        filtered = deduplicate_results(filtered)

    final = filtered[: cfg.max_results]

    return extract_knowledge_context(
        final,
        template=cfg.context_template,
        include_metadata=cfg.include_metadata,
    )
