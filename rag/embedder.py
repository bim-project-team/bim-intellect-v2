"""Multilingual embeddings and the versioned Chroma regulation index."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Iterable

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from .chunker import Chunk, normalize_persian_text
from .config import SETTINGS, RAGSettings
from .openrouter_client import LLMConfigError, LLMRequestError, call_with_retries, get_client, logger

CHROMA_DIR = SETTINGS.chroma_dir
COLLECTION_NAME = SETTINGS.collection_name
EMBED_BATCH_SIZE = SETTINGS.embedding_batch_size


@lru_cache(maxsize=4)
def _local_model(model_name: str, device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise LLMConfigError(
            "Local multilingual embeddings require sentence-transformers. "
            "Install project requirements or set RAG_EMBEDDING_PROVIDER=openrouter."
        ) from exc
    return SentenceTransformer(model_name, device=device)


class MultilingualEmbeddingFunction(EmbeddingFunction):
    """Chroma embedding function supporting local Persian/multilingual models."""

    def __init__(self, settings: RAGSettings = SETTINGS):
        self.settings = settings

    @staticmethod
    def name() -> str:
        return "bim-intellect-multilingual-v2"

    def get_config(self) -> dict[str, Any]:
        return {
            "provider": self.settings.embedding_provider,
            "model": self.settings.embedding_model,
            "device": self.settings.embedding_device,
            "batch_size": self.settings.embedding_batch_size,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "MultilingualEmbeddingFunction":
        settings = RAGSettings(
            embedding_provider=str(config["provider"]),
            embedding_model=str(config["model"]),
            embedding_device=str(config.get("device", "cpu")),
            embedding_batch_size=int(config.get("batch_size", 64)),
        )
        return MultilingualEmbeddingFunction(settings)

    def __call__(self, input: Documents) -> Embeddings:
        texts = [normalize_persian_text(value or "") for value in input]
        if not texts:
            return []
        if self.settings.embedding_provider.lower() == "local":
            model = _local_model(self.settings.embedding_model, self.settings.embedding_device)
            vectors = model.encode(
                texts,
                batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return vectors.tolist()
        if self.settings.embedding_provider.lower() != "openrouter":
            raise LLMConfigError(
                f"Unsupported RAG_EMBEDDING_PROVIDER={self.settings.embedding_provider!r}"
            )

        embeddings: Embeddings = []
        for start in range(0, len(texts), self.settings.embedding_batch_size):
            batch = texts[start:start + self.settings.embedding_batch_size]

            def do_embed(batch=batch):
                return get_client().embeddings.create(
                    model=self.settings.embedding_model,
                    input=batch,
                )

            response = call_with_retries(do_embed, op_name="multilingual embeddings")
            embeddings.extend(item.embedding for item in response.data)
        return embeddings


# Backwards-compatible import name used by older callers.
OpenRouterEmbeddingFunction = MultilingualEmbeddingFunction


def get_client_db(settings: RAGSettings = SETTINGS) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=settings.chroma_dir)


def _collection_metadata(settings: RAGSettings) -> dict[str, str]:
    return {
        "rag_index_version": settings.index_version,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "hnsw:space": "cosine",
    }


def get_or_create_collection(
    client: chromadb.ClientAPI | None = None,
    settings: RAGSettings = SETTINGS,
):
    client = client or get_client_db(settings)
    collection = client.get_or_create_collection(
        name=settings.collection_name,
        embedding_function=MultilingualEmbeddingFunction(settings),
        metadata=_collection_metadata(settings),
    )
    metadata = collection.metadata or {}
    expected = _collection_metadata(settings)
    mismatches = [
        key for key in ("rag_index_version", "embedding_provider", "embedding_model")
        if str(metadata.get(key, "")) != str(expected[key])
    ]
    if collection.count() and mismatches:
        raise LLMConfigError(
            "The regulation vector index is incompatible with current RAG settings "
            f"({', '.join(mismatches)} changed). Rebuild it with: "
            "python -m rag.indexer --source-dir dataset/sources --rebuild"
        )
    return collection


def _embedding_text(chunk: Chunk) -> str:
    labels = [chunk.document_title or chunk.source, chunk.chapter, chunk.clause_id, chunk.heading]
    prefix = " | ".join(str(value) for value in labels if value and value != "unknown")
    return f"{prefix}\n{chunk.text}" if prefix else chunk.text


def _metadata(chunk: Chunk, ingested_at: str = "") -> dict[str, Any]:
    values: dict[str, Any] = {
        "source": chunk.source or "unknown",
        "document_id": chunk.document_id or "unknown",
        "document_title": chunk.document_title or chunk.source or "unknown",
        "page_number": int(chunk.page_number),
        "clause_id": chunk.clause_id or "unknown",
        "chapter": chunk.chapter or "",
        "section_id": chunk.section_id or chunk.clause_id or "unknown",
        "heading": chunk.heading or "",
        "chunk_index": int(chunk.chunk_index),
        "section_chunk_index": int(chunk.section_chunk_index),
        "previous_chunk_id": chunk.previous_chunk_id or "",
        "next_chunk_id": chunk.next_chunk_id or "",
        "previous_section_chunk_id": chunk.previous_section_chunk_id or "",
        "next_section_chunk_id": chunk.next_section_chunk_id or "",
        "content_hash": chunk.content_hash or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        "is_toc": bool(chunk.is_toc),
        "ingested_at": ingested_at,
    }
    # Chroma metadata values must be scalar and cannot be None.
    return values


def embed_and_store(chunks: list[Chunk], settings: RAGSettings = SETTINGS) -> int:
    if not chunks:
        logger.info("No regulation chunks to index.")
        return 0
    collection = get_or_create_collection(settings=settings)
    total = 0
    ingested_at = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(chunks), settings.embedding_batch_size):
        batch = chunks[start:start + settings.embedding_batch_size]
        embedding_texts = [_embedding_text(chunk) for chunk in batch]
        embeddings = MultilingualEmbeddingFunction(settings)(embedding_texts)
        try:
            collection.upsert(
                ids=[chunk.chunk_id for chunk in batch],
                documents=[chunk.text for chunk in batch],
                metadatas=[_metadata(chunk, ingested_at) for chunk in batch],
                embeddings=embeddings,
            )
        except Exception as exc:
            raise LLMRequestError(f"Failed to store regulation embeddings: {exc}") from exc
        total += len(batch)
        logger.info("Indexed %d/%d regulation chunks", total, len(chunks))
    return total


def _empty_query_result() -> dict[str, list[list[Any]]]:
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def query_similar(
    query_text: str,
    n_results: int | None = None,
    settings: RAGSettings = SETTINGS,
):
    if not query_text or not query_text.strip():
        raise LLMRequestError("Semantic retrieval requires a non-empty query.")
    collection = get_or_create_collection(settings=settings)
    count = collection.count()
    if count == 0:
        logger.warning("Regulation collection '%s' is empty", settings.collection_name)
        return _empty_query_result()
    return collection.query(
        query_texts=[normalize_persian_text(query_text)],
        n_results=min(n_results or settings.candidate_count, count),
        include=["documents", "metadatas", "distances"],
    )


def _flatten(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result.get("ids"):
        return []
    return [
        {"id": chunk_id, "document": document, "metadata": metadata or {}, "distance": distance}
        for chunk_id, document, metadata, distance in zip(
            result["ids"][0],
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        )
    ]


def get_chunks_by_ids(ids: Iterable[str], settings: RAGSettings = SETTINGS) -> list[dict[str, Any]]:
    ids = [value for value in dict.fromkeys(ids) if value]
    if not ids:
        return []
    collection = get_or_create_collection(settings=settings)
    result = collection.get(ids=ids, include=["documents", "metadatas"])
    records = {
        chunk_id: {"id": chunk_id, "document": document, "metadata": metadata or {}}
        for chunk_id, document, metadata in zip(
            result.get("ids", []), result.get("documents", []), result.get("metadatas", [])
        )
    }
    return [records[value] for value in ids if value in records]


def get_section_chunks(
    document_id: str,
    section_id: str,
    settings: RAGSettings = SETTINGS,
) -> list[dict[str, Any]]:
    if not document_id or not section_id or section_id == "unknown":
        return []
    collection = get_or_create_collection(settings=settings)
    result = collection.get(
        where={"$and": [{"document_id": document_id}, {"section_id": section_id}]},
        include=["documents", "metadatas"],
    )
    records = [
        {"id": chunk_id, "document": document, "metadata": metadata or {}}
        for chunk_id, document, metadata in zip(
            result.get("ids", []), result.get("documents", []), result.get("metadatas", [])
        )
    ]
    return sorted(records, key=lambda item: int(item["metadata"].get("section_chunk_index", 0)))


def get_all_chunks(
    limit: int | None = None,
    settings: RAGSettings = SETTINGS,
) -> list[dict[str, Any]]:
    collection = get_or_create_collection(settings=settings)
    kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
    if limit is not None:
        kwargs["limit"] = limit
    result = collection.get(**kwargs)
    return [
        {"id": chunk_id, "document": document, "metadata": metadata or {}}
        for chunk_id, document, metadata in zip(
            result.get("ids", []), result.get("documents", []), result.get("metadatas", [])
        )
    ]


def collection_status(settings: RAGSettings = SETTINGS) -> dict[str, Any]:
    collection = get_or_create_collection(settings=settings)
    return {
        "collection": settings.collection_name,
        "count": collection.count(),
        "path": settings.chroma_dir,
        "metadata": collection.metadata or {},
    }


def list_indexed_documents(settings: RAGSettings = SETTINGS) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in get_all_chunks(settings=settings):
        metadata = record["metadata"]
        document_id = str(metadata.get("document_id") or metadata.get("source") or "unknown")
        item = grouped.setdefault(document_id, {
            "document_id": document_id,
            "filename": metadata.get("source") or metadata.get("document_title") or document_id,
            "status": "indexed",
            "processing_status": "embedded",
            "chunk_count": 0,
            "pages": set(),
            "ingested_at": metadata.get("ingested_at") or None,
        })
        item["chunk_count"] += 1
        if metadata.get("page_number"):
            item["pages"].add(int(metadata["page_number"]))
        if metadata.get("ingested_at") and not item.get("ingested_at"):
            item["ingested_at"] = metadata["ingested_at"]
    output = []
    for item in grouped.values():
        item["page_count"] = len(item.pop("pages"))
        output.append(item)
    return sorted(output, key=lambda item: str(item["filename"]).casefold())


def delete_document(document_id: str, settings: RAGSettings = SETTINGS) -> int:
    """Remove one document's stale chunks before an explicit replacement upload."""
    if not document_id:
        return 0
    collection = get_or_create_collection(settings=settings)
    existing = collection.get(where={"document_id": document_id}, include=[])
    ids = existing.get("ids", [])
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def main() -> None:
    from .indexer import main as index_main
    index_main()


if __name__ == "__main__":
    main()
