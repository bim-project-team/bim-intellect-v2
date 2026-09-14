"""Environment-backed configuration for the production RAG pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class ModelProfile:
    mode: str
    router_model: str
    final_model: str


@dataclass(frozen=True)
class RAGSettings:
    router_model: str = os.getenv("RAG_ROUTER_MODEL", os.getenv("OPENROUTER_CHAT_MODEL", "openai/gpt-4o-mini"))
    final_model: str = os.getenv("RAG_FINAL_MODEL", os.getenv("OPENROUTER_CHAT_MODEL", "openai/gpt-4o-mini"))
    strong_router_model: str = os.getenv("RAG_STRONG_ROUTER_MODEL", "google/gemini-2.5-flash")
    strong_final_model: str = os.getenv("RAG_STRONG_FINAL_MODEL", "anthropic/claude-sonnet-4.5")

    embedding_provider: str = os.getenv("RAG_EMBEDDING_PROVIDER", "local")
    embedding_model: str = os.getenv(
        "RAG_EMBEDDING_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    embedding_device: str = os.getenv("RAG_EMBEDDING_DEVICE", "cpu")
    embedding_batch_size: int = _int("RAG_EMBEDDING_BATCH_SIZE", 64)

    collection_name: str = os.getenv("RAG_COLLECTION_NAME", "regulations_v2")
    index_version: str = os.getenv("RAG_INDEX_VERSION", "3")
    chroma_dir: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")

    candidate_count: int = _int("RAG_CANDIDATE_COUNT", 32)
    rerank_count: int = _int("RAG_RERANK_COUNT", 10)
    max_query_variants: int = _int("RAG_MAX_QUERY_VARIANTS", 4)
    max_section_chunks: int = _int("RAG_MAX_SECTION_CHUNKS", 24)
    neighbor_window: int = _int("RAG_NEIGHBOR_WINDOW", 2)
    max_context_chars: int = _int("RAG_MAX_CONTEXT_CHARS", 28000)
    weak_relevance_threshold: float = _float("RAG_WEAK_RELEVANCE_THRESHOLD", 0.18)
    lexical_pool_limit: int = _int("RAG_LEXICAL_POOL_LIMIT", 5000)

    reranker_provider: str = os.getenv("RAG_RERANKER_PROVIDER", "hybrid")
    reranker_model: str = os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_device: str = os.getenv("RAG_RERANKER_DEVICE", "cpu")
    dense_weight: float = _float("RAG_DENSE_WEIGHT", 0.45)
    lexical_weight: float = _float("RAG_LEXICAL_WEIGHT", 0.25)
    heading_weight: float = _float("RAG_HEADING_WEIGHT", 0.20)
    document_weight: float = _float("RAG_DOCUMENT_WEIGHT", 0.05)
    multi_query_weight: float = _float("RAG_MULTI_QUERY_WEIGHT", 0.05)

    memory_recent_messages: int = _int("RAG_MEMORY_RECENT_MESSAGES", 10)
    memory_summary_chars: int = _int("RAG_MEMORY_SUMMARY_CHARS", 4000)
    memory_max_conversations: int = _int("RAG_MEMORY_MAX_CONVERSATIONS", 1000)
    memory_ttl_seconds: int = _int("RAG_MEMORY_TTL_SECONDS", 21600)


SETTINGS = RAGSettings()


def get_model_profile(use_strong_models: bool, settings: RAGSettings = SETTINGS) -> ModelProfile:
    if use_strong_models:
        return ModelProfile("strong", settings.strong_router_model, settings.strong_final_model)
    return ModelProfile("standard", settings.router_model, settings.final_model)
