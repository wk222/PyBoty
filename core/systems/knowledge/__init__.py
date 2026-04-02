"""Knowledge modules."""

from .embedding_resolver import (
    BaseEmbeddingProvider,
    EmbeddingError,
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
    VoyageEmbeddingProvider,
    resolve_embeddings,
)
from .faiss_store import FAISSVectorStore
from .sqlite_vec_store import SQLiteVecVectorStore
from .vector_store import (
    ChromaVectorStore,
    Document,
    InMemoryVectorStore,
    SearchResult,
    VectorStoreBackend,
    create_vector_store,
)

__all__ = [
    "BaseEmbeddingProvider",
    "ChromaVectorStore",
    "Document",
    "EmbeddingError",
    "EmbeddingProvider",
    "FAISSVectorStore",
    "GeminiEmbeddingProvider",
    "InMemoryVectorStore",
    "OllamaEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "SQLiteVecVectorStore",
    "SearchResult",
    "SentenceTransformerEmbeddingProvider",
    "VectorStoreBackend",
    "VoyageEmbeddingProvider",
    "create_vector_store",
    "resolve_embeddings",
]
