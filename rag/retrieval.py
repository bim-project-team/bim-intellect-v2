"""Complete regulation retrieval: multi-query, reranking, expansion and assembly."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from .config import SETTINGS, RAGSettings
from .embedder import get_all_chunks, get_chunks_by_ids, get_section_chunks, query_similar
from .reranker import Candidate, rerank_candidates

logger = logging.getLogger("bim_intellect.rag.retrieval")


@dataclass
class RetrievalDiagnostics:
    retrieval_queries: list[str] = field(default_factory=list)
    vector_candidates: int = 0
    lexical_candidates: int = 0
    reranked_results: int = 0
    expanded_chunks: int = 0
    final_context_chunks: int = 0
    final_context_sections: int = 0
    fallback_used: bool = False
    documents_pages: list[str] = field(default_factory=list)


@dataclass
class RegulationRetrieval:
    context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[Candidate] = field(default_factory=list)
    diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)

    @property
    def strongest_score(self) -> float:
        return max((chunk.rerank_score for chunk in self.chunks), default=0.0)


def _merge_vector_results(queries: list[str], settings: RAGSettings) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for query in queries:
        results = query_similar(query, n_results=settings.candidate_count, settings=settings)
        rows = zip(
            (results.get("ids") or [[]])[0],
            (results.get("documents") or [[]])[0],
            (results.get("metadatas") or [[]])[0],
            (results.get("distances") or [[]])[0],
        )
        for rank, (chunk_id, text, metadata, distance) in enumerate(rows, start=1):
            metadata = metadata or {}
            if metadata.get("is_toc"):
                continue
            candidate = merged.get(chunk_id)
            score = max(0.0, 1.0 - float(distance))
            if candidate is None:
                candidate = Candidate(chunk_id, text, metadata, dense_score=score)
                merged[chunk_id] = candidate
            candidate.dense_score = max(candidate.dense_score, score)
            candidate.query_hits += 1
            candidate.reciprocal_rank += 1.0 / (60 + rank)
            candidate.matched_queries.add(query)
    return list(merged.values())


def _lexical_fallback(query: str, settings: RAGSettings) -> list[Candidate]:
    records = get_all_chunks(settings=settings, limit=settings.lexical_pool_limit)
    candidates = [
        Candidate(record["id"], record["document"], record["metadata"], expansion_reason="lexical_fallback")
        for record in records
        if not record["metadata"].get("is_toc")
    ]
    # Candidate generation must stay cheap even when the final reranker is a
    # cross-encoder; cross-encoding the entire corpus would defeat the fallback cap.
    hybrid_settings = replace(settings, reranker_provider="hybrid")
    return rerank_candidates(query, candidates, limit=settings.candidate_count, settings=hybrid_settings)


def _merge_candidates(primary: list[Candidate], secondary: list[Candidate]) -> list[Candidate]:
    merged = {candidate.chunk_id: candidate for candidate in primary}
    for candidate in secondary:
        existing = merged.get(candidate.chunk_id)
        if existing is None:
            merged[candidate.chunk_id] = candidate
        else:
            existing.lexical_score = max(existing.lexical_score, candidate.lexical_score)
            existing.rerank_score = max(existing.rerank_score, candidate.rerank_score)
    return list(merged.values())


def _candidate_from_record(record: dict[str, Any], score: float, reason: str) -> Candidate:
    return Candidate(
        chunk_id=record["id"],
        text=record["document"],
        metadata=record["metadata"],
        dense_score=score,
        rerank_score=score,
        expansion_reason=reason,
    )


def _expand(
    ranked: list[Candidate],
    completeness_requested: bool,
    settings: RAGSettings,
) -> list[Candidate]:
    expanded: dict[str, Candidate] = {candidate.chunk_id: candidate for candidate in ranked}
    if completeness_requested:
        sections: list[tuple[str, str, float]] = []
        seen_sections: set[tuple[str, str]] = set()
        for candidate in ranked:
            key = (
                str(candidate.metadata.get("document_id", "")),
                str(candidate.metadata.get("section_id", "")),
            )
            if not all(key) or key[1] == "unknown" or key in seen_sections:
                continue
            seen_sections.add(key)
            sections.append((*key, candidate.rerank_score))
            if len(sections) >= 3:
                break
        for document_id, section_id, score in sections:
            records = get_section_chunks(document_id, section_id, settings=settings)
            for record in records[:settings.max_section_chunks]:
                if record["metadata"].get("is_toc"):
                    continue
                expanded.setdefault(
                    record["id"],
                    _candidate_from_record(record, score * 0.92, "complete_section"),
                )
    else:
        frontier = list(ranked)
        for depth in range(max(0, settings.neighbor_window)):
            link_ids: list[str] = []
            for candidate in frontier:
                metadata = candidate.metadata
                link_ids.extend([
                    str(metadata.get("previous_section_chunk_id", "")),
                    str(metadata.get("next_section_chunk_id", "")),
                ])
            next_frontier: list[Candidate] = []
            for record in get_chunks_by_ids(link_ids, settings=settings):
                if record["metadata"].get("is_toc") or record["id"] in expanded:
                    continue
                section_key = record["metadata"].get("section_id")
                seed_score = max(
                    (item.rerank_score for item in ranked if item.metadata.get("section_id") == section_key),
                    default=0.0,
                )
                candidate = _candidate_from_record(
                    record, seed_score * (0.88 ** (depth + 1)), "section_neighbor"
                )
                expanded[record["id"]] = candidate
                next_frontier.append(candidate)
            frontier = next_frontier
            if not frontier:
                break
    return list(expanded.values())


def _document_order(candidate: Candidate) -> tuple[str, int, int]:
    metadata = candidate.metadata
    return (
        str(metadata.get("document_id", "")),
        int(metadata.get("page_number", 0)),
        int(metadata.get("chunk_index", 0)),
    )


def assemble_context(
    candidates: list[Candidate],
    settings: RAGSettings = SETTINGS,
) -> tuple[str, list[dict[str, Any]], list[Candidate]]:
    """Prefer complete high-scoring sections, deduplicating exact text."""
    grouped: dict[tuple[str, str], list[Candidate]] = {}
    section_scores: dict[tuple[str, str], float] = {}
    for candidate in candidates:
        metadata = candidate.metadata
        key = (
            str(metadata.get("document_id", metadata.get("source", "unknown"))),
            str(metadata.get("section_id", candidate.chunk_id)),
        )
        grouped.setdefault(key, []).append(candidate)
        section_scores[key] = max(section_scores.get(key, 0.0), candidate.rerank_score)

    ordered_sections = sorted(grouped, key=lambda key: section_scores[key], reverse=True)
    selected: list[Candidate] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    used_chars = 0
    for section_key in ordered_sections:
        section = sorted(grouped[section_key], key=_document_order)
        section_cost = sum(len(item.text) + 120 for item in section)
        if selected and used_chars + section_cost > settings.max_context_chars:
            continue
        for item in section:
            content_hash = str(item.metadata.get("content_hash", ""))
            if item.chunk_id in seen_ids or (content_hash and content_hash in seen_hashes):
                continue
            selected.append(item)
            seen_ids.add(item.chunk_id)
            if content_hash:
                seen_hashes.add(content_hash)
            used_chars += len(item.text) + 120
        if used_chars >= settings.max_context_chars:
            break

    blocks: list[str] = []
    sources: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str, int]] = set()
    for source_index, item in enumerate(selected, start=1):
        metadata = item.metadata
        clause = str(metadata.get("clause_id", "unknown"))
        page = int(metadata.get("page_number", 0))
        source = str(metadata.get("source", "Unknown source"))
        section = str(metadata.get("section_id", "unknown"))
        blocks.append(
            f'<SOURCE id="source_{source_index}">\n'
            f"Document: {source}\n"
            f"Clause: {clause}\n"
            f"Page: {page}\n"
            f"Section: {section}\n"
            f"Chunk-ID: {item.chunk_id}\n"
            f"Text:\n{item.text}\n"
            f"</SOURCE>"
        )
        source_key = (source, clause, page)
        if clause not in {"", "unknown", "None"} and page and source_key not in seen_sources:
            seen_sources.add(source_key)
            sources.append({
                "type": "regulation",
                "source": source,
                "clause_id": clause,
                "page_number": page,
            })
    return "\n\n".join(blocks), sources, selected


def retrieve_regulations(
    standalone_query: str,
    retrieval_queries: list[str] | None = None,
    *,
    completeness_requested: bool = False,
    settings: RAGSettings = SETTINGS,
) -> RegulationRetrieval:
    queries = [standalone_query, *(retrieval_queries or [])]
    queries = [value.strip() for value in dict.fromkeys(queries) if value and value.strip()]
    queries = queries[:settings.max_query_variants]
    diagnostics = RetrievalDiagnostics(retrieval_queries=queries)
    candidates = _merge_vector_results(queries, settings)
    diagnostics.vector_candidates = len(candidates)
    ranked = rerank_candidates(
        standalone_query,
        candidates,
        limit=settings.rerank_count,
        settings=settings,
    )

    # One bounded fallback pass broadens lexical recall if semantic evidence is weak.
    if not ranked or max((item.dense_score for item in candidates), default=0.0) < settings.weak_relevance_threshold:
        fallback = _lexical_fallback(standalone_query, settings)
        diagnostics.fallback_used = True
        diagnostics.lexical_candidates = len(fallback)
        candidates = _merge_candidates(candidates, fallback)
        ranked = rerank_candidates(
            standalone_query,
            candidates,
            limit=settings.rerank_count,
            settings=settings,
        )
    diagnostics.reranked_results = len(ranked)

    expanded = _expand(ranked, completeness_requested, settings)
    diagnostics.expanded_chunks = max(0, len(expanded) - len(ranked))
    context, sources, selected = assemble_context(expanded, settings)
    diagnostics.final_context_chunks = len(selected)
    diagnostics.final_context_sections = len({
        (item.metadata.get("document_id"), item.metadata.get("section_id")) for item in selected
    })
    diagnostics.documents_pages = sorted({
        f"{item.metadata.get('source')}:{item.metadata.get('page_number')}" for item in selected
    })
    logger.info(
        "Regulation retrieval: queries=%d candidates=%d reranked=%d expanded=%d final=%d sections=%d fallback=%s",
        len(queries), diagnostics.vector_candidates, diagnostics.reranked_results,
        diagnostics.expanded_chunks, diagnostics.final_context_chunks,
        diagnostics.final_context_sections, diagnostics.fallback_used,
    )
    logger.info("Regulation context documents/pages: %s", diagnostics.documents_pages)
    return RegulationRetrieval(context, sources, selected, diagnostics)
