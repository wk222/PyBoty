"""Knowledge retrieval tools exposed to the Agent.

Provides LangChain tools for:
  - knowledge_search: semantic search over ingested documents
  - knowledge_ingest: add files/text to the knowledge base
  - knowledge_list: list collections and document counts
  - knowledge_delete: remove documents or collections
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .document_pipeline import DocumentPipeline
from .vector_store import VectorStoreBackend


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="搜索查询文本")
    collection: str = Field(default="default", description="知识库集合名称")
    top_k: int = Field(default=5, description="返回结果数量", ge=1, le=20)


class KnowledgeIngestInput(BaseModel):
    path: str = Field(description="要摄入的文件路径")
    collection: str = Field(default="default", description="目标知识库集合")


class KnowledgeIngestTextInput(BaseModel):
    text: str = Field(description="要摄入的文本内容")
    collection: str = Field(default="default", description="目标知识库集合")
    source: str = Field(default="user_input", description="文本来源标识")


class KnowledgeListInput(BaseModel):
    pass


class KnowledgeDeleteInput(BaseModel):
    collection: str = Field(description="要操作的集合名称")
    doc_ids: list[str] = Field(default_factory=list, description="要删除的文档ID列表（空则删除整个集合）")


def _search_knowledge(
    vector_store: VectorStoreBackend,
    query: str,
    collection: str = "default",
    top_k: int = 5,
) -> str:
    results = vector_store.search(query, collection=collection, top_k=top_k)
    if not results:
        return f"未在知识库 '{collection}' 中找到相关内容。"

    output_parts = [f"在知识库 '{collection}' 中找到 {len(results)} 条相关结果：\n"]
    for i, r in enumerate(results, 1):
        meta = r.document.metadata
        source = meta.get("filename", meta.get("source", "unknown"))
        output_parts.append(f"--- 结果 {i} (相关度: {r.score:.2f}, 来源: {source}) ---")
        output_parts.append(r.document.page_content)
        output_parts.append("")
    return "\n".join(output_parts)


def _ingest_knowledge(
    pipeline: DocumentPipeline,
    path: str,
    collection: str = "default",
) -> str:
    result = pipeline.ingest(path, collection=collection)
    if result.errors:
        return f"摄入失败: {'; '.join(result.errors)}"
    return f"成功摄入文件 '{result.path}' 到知识库 '{collection}'\n格式: {result.format}, 分块数: {result.chunk_count}"


def _ingest_text(
    pipeline: DocumentPipeline,
    text: str,
    collection: str = "default",
    source: str = "user_input",
) -> str:
    result = pipeline.ingest_text(text, collection=collection, source=source)
    if result.errors:
        return f"摄入失败: {'; '.join(result.errors)}"
    return f"成功摄入文本到知识库 '{collection}', 分块数: {result.chunk_count}"


def _list_knowledge(vector_store: VectorStoreBackend) -> str:
    collections = vector_store.list_collections()
    if not collections:
        return "知识库为空，尚未创建任何集合。"

    lines = ["知识库集合列表："]
    for name in sorted(collections):
        count = vector_store.count(name)
        lines.append(f"  - {name}: {count} 个文档块")
    return "\n".join(lines)


def _delete_knowledge(
    vector_store: VectorStoreBackend,
    collection: str,
    doc_ids: list[str] | None = None,
) -> str:
    if doc_ids:
        deleted = vector_store.delete(doc_ids, collection=collection)
        return f"从 '{collection}' 中删除了 {deleted} 个文档块。"
    else:
        success = vector_store.delete_collection(collection)
        if success:
            return f"已删除整个集合 '{collection}'。"
        return f"删除集合 '{collection}' 失败（可能不存在）。"


def get_knowledge_tools(
    vector_store: VectorStoreBackend,
    pipeline: DocumentPipeline | None = None,
) -> list[StructuredTool]:
    """Build the knowledge retrieval tool set for Agent injection."""

    if pipeline is None:
        pipeline = DocumentPipeline(vector_store)

    tools = [
        StructuredTool.from_function(
            func=lambda query, collection="default", top_k=5: _search_knowledge(vector_store, query, collection, top_k),
            name="knowledge_search",
            description=(
                "在知识库中语义搜索相关文档。"
                "用于查找已摄入的文档、笔记、代码等。"
                "参数: query(搜索文本), collection(集合名,默认default), top_k(结果数,默认5)"
            ),
            args_schema=KnowledgeSearchInput,
        ),
        StructuredTool.from_function(
            func=lambda path, collection="default": _ingest_knowledge(pipeline, path, collection),
            name="knowledge_ingest",
            description=(
                "将文件摄入到知识库中用于后续检索。"
                "支持: .txt, .md, .py, .json, .csv, .html, .pdf 等格式。"
                "参数: path(文件路径), collection(集合名,默认default)"
            ),
            args_schema=KnowledgeIngestInput,
        ),
        StructuredTool.from_function(
            func=lambda text, collection="default", source="user_input": _ingest_text(
                pipeline, text, collection, source
            ),
            name="knowledge_ingest_text",
            description=("将文本内容直接摄入到知识库中。参数: text(文本内容), collection(集合名), source(来源标识)"),
            args_schema=KnowledgeIngestTextInput,
        ),
        StructuredTool.from_function(
            func=lambda: _list_knowledge(vector_store),
            name="knowledge_list",
            description="列出知识库中所有集合及其文档数量。",
            args_schema=KnowledgeListInput,
        ),
        StructuredTool.from_function(
            func=lambda collection, doc_ids=None: _delete_knowledge(vector_store, collection, doc_ids or []),
            name="knowledge_delete",
            description=(
                "删除知识库中的文档或整个集合。参数: collection(集合名), doc_ids(文档ID列表,空则删除整个集合)"
            ),
            args_schema=KnowledgeDeleteInput,
        ),
    ]
    return tools
