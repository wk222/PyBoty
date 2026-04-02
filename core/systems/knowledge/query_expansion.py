"""Query Expansion and Context Compaction for Advanced RAG.

Provides mechanisms to rewrite user queries for better vector retrieval
and to compact retrieved context to fit within LLM context windows.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


class QueryExpansionEngine:
    """Expands or rewrites user queries to improve retrieval recall."""

    def __init__(self, llm: BaseChatModel | None = None):
        self.llm = llm
        self._rewrite_prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert search query rewriter. Your task is to take a user's "
                    "query and rewrite it into 3 distinct, highly effective search queries "
                    "that will be used to retrieve relevant documents from a vector database. "
                    "Focus on synonyms, related concepts, and removing conversational filler. "
                    "Return ONLY the queries, separated by newlines.",
                ),
                ("human", "{query}"),
            ]
        )

    def expand_query(self, query: str) -> list[str]:
        """Generate multiple search queries from a single user query."""
        if not self.llm:
            logger.debug("Query expansion skipped (no LLM configured).")
            return [query]

        try:
            response = self.llm.invoke(self._rewrite_prompt.format_messages(query=query))
            content = response.content if hasattr(response, "content") else str(response)
            queries = [q.strip("- *1234567890.") for q in content.split("\n") if q.strip()]
            
            # Always include the original query
            if query not in queries:
                queries.insert(0, query)
                
            logger.debug("Expanded query '%s' into %d variants.", query, len(queries))
            return queries[:4]  # Limit to 4 queries max
        except Exception as exc:
            logger.warning("Query expansion failed: %s", exc)
            return [query]


class ContextCompactor:
    """Compacts retrieved documents to maximize relevance within token limits."""

    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens

    def compact(self, documents: list[dict[str, Any]], query: str) -> str:
        """Format and truncate documents to fit within the context window."""
        # Simple heuristic compaction: prioritize higher ranked docs
        # In a full implementation, this could use an LLM or cross-encoder to extract snippets.
        
        compacted_text = ""
        current_length = 0
        
        for i, doc in enumerate(documents):
            content = doc.get("content", "")
            source = doc.get("metadata", {}).get("source", f"Doc {i+1}")
            
            # Rough token estimation (4 chars per token)
            estimated_tokens = len(content) // 4
            
            if current_length + estimated_tokens > self.max_tokens:
                # Truncate the last document that fits partially
                remaining_tokens = self.max_tokens - current_length
                if remaining_tokens > 50:
                    allowed_chars = remaining_tokens * 4
                    snippet = f"Source: {source}\n{content[:allowed_chars]}...\n\n"
                    compacted_text += snippet
                break
                
            snippet = f"Source: {source}\n{content}\n\n"
            compacted_text += snippet
            current_length += estimated_tokens
            
        return compacted_text.strip()
