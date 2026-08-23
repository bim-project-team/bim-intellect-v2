"""Configurable second-stage multilingual reranking."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer

from .chunker import normalize_persian_text
from .config import SETTINGS, RAGSettings

logger = logging.getLogger("bim_intellect.rag.reranker")


@dataclass
class Candidate:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    dense_score: float = 0.0
    query_hits: int = 0
    reciprocal_rank: float = 0.0
    lexical_score: float = 0.0
    rerank_score: float = 0.0
    matched_queries: set[str] = field(default_factory=set)
    expansion_reason: str = "retrieved"


def _scaled(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _lexical_scores(query: str, candidates: list[Candidate]) -> list[float]:
    """Character n-grams tolerate Persian spacing and inflection variation."""
    if not candidates:
        return []
    texts = [normalize_persian_text(query)] + [normalize_persian_text(item.text) for item in candidates]
    try:
        matrix = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1).fit_transform(texts)
        return (matrix[1:] @ matrix[0].T).toarray().ravel().tolist()
    except ValueError:
        return [0.0] * len(candidates)


@lru_cache(maxsize=2)
def _cross_encoder(model_name: str, device: str):
    from sentence_transformers import CrossEncoder
    return CrossEncoder(model_name, device=device)


def rerank_candidates(
    query: str,
    candidates: list[Candidate],
    *,
    limit: int | None = None,
    settings: RAGSettings = SETTINGS,
) -> list[Candidate]:
    if not candidates:
        return []
    provider = settings.reranker_provider.lower()
    if provider in {"cross-encoder", "cross_encoder", "bge"}:
        try:
            model = _cross_encoder(settings.reranker_model, settings.reranker_device)
            scores = model.predict([(query, item.text) for item in candidates])
            for item, score in zip(candidates, scores):
                item.rerank_score = float(score)
        except Exception as exc:  # optional dependency/model must not take RAG down
            logger.warning("Cross-encoder reranker unavailable (%s); using hybrid reranking", exc)
            provider = "hybrid"
    if provider == "hybrid":
        lexical = _scaled(_lexical_scores(query, candidates))
        dense = _scaled([item.dense_score for item in candidates])
        agreement = _scaled([
            item.query_hits + min(item.reciprocal_rank, 1.0) for item in candidates
        ])
        for index, item in enumerate(candidates):
            item.lexical_score = lexical[index]
            item.rerank_score = (
                settings.dense_weight * dense[index]
                + settings.lexical_weight * lexical[index]
                + settings.multi_query_weight * agreement[index]
            )
    elif provider not in {"cross-encoder", "cross_encoder", "bge"}:
        raise ValueError(f"Unsupported RAG_RERANKER_PROVIDER={settings.reranker_provider!r}")
    return sorted(candidates, key=lambda item: item.rerank_score, reverse=True)[:limit]
