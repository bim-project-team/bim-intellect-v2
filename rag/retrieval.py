"""Complete regulation retrieval: multi-query, reranking, expansion and assembly."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from .config import SETTINGS, RAGSettings
from .document_metadata import metadata_domain, normalize_domain_filter
from .embedder import (
    get_all_chunks, get_chunks_by_ids, get_section_chunks, get_section_tree_chunks,
    query_similar,
)
from .chunker import normalize_persian_text, persian_decimal_aliases
from .reranker import Candidate, normalize_retrieval_query, rerank_candidates

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
    document_domains: list[str] = field(default_factory=list)
    dense_top: list[dict[str, Any]] = field(default_factory=list)
    lexical_top: list[dict[str, Any]] = field(default_factory=list)
    reranked_top: list[dict[str, Any]] = field(default_factory=list)
    selected_chunks: list[dict[str, Any]] = field(default_factory=list)
    numeric_aliases: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RegulationRetrieval:
    context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[Candidate] = field(default_factory=list)
    diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)

    @property
    def strongest_score(self) -> float:
        return max((chunk.rerank_score for chunk in self.chunks), default=0.0)


def _merge_vector_results(
    queries: list[str], settings: RAGSettings, document_domains: tuple[str, ...] = (),
) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for query in queries:
        results = query_similar(
            query, n_results=settings.candidate_count, settings=settings,
            document_domains=document_domains,
        )
        rows = zip(
            (results.get("ids") or [[]])[0],
            (results.get("documents") or [[]])[0],
            (results.get("metadatas") or [[]])[0],
            (results.get("distances") or [[]])[0],
        )
        for rank, (chunk_id, text, metadata, distance) in enumerate(rows, start=1):
            metadata = metadata or {}
            if metadata.get("is_toc") or (
                document_domains and metadata_domain(metadata) not in set(document_domains)
            ):
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


def _lexical_fallback(
    query: str, settings: RAGSettings, document_domains: tuple[str, ...] = (),
) -> list[Candidate]:
    records = get_all_chunks(
        settings=settings, limit=settings.lexical_pool_limit,
        document_domains=document_domains,
    )
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


def _ranked_descendant_exists(
    ranked: list[Candidate], document_id: str, section_id: str,
) -> bool:
    prefix = f"{section_id}-"
    return any(
        str(item.metadata.get("document_id", "")) == document_id
        and str(item.metadata.get("section_id", "")).startswith(prefix)
        for item in ranked
    )


def _inferred_complete_parents(
    ranked: list[Candidate],
) -> list[tuple[str, str, float]]:
    """Infer a shared immediate parent when several ranked leaves agree on it.

    This catches enumeration questions whose parent heading is short and embeds
    poorly (for example, several ``15-2-2-5-*`` motor-room clauses) without
    expanding an arbitrary high-level chapter from a single broad hit.
    """
    groups: dict[tuple[str, str], dict[str, float]] = {}
    for candidate in ranked:
        document_id = str(candidate.metadata.get("document_id", ""))
        section_id = str(candidate.metadata.get("section_id", ""))
        parent = str(candidate.metadata.get("parent_section_id", ""))
        if not parent and "-" in section_id:
            parent = section_id.rsplit("-", 1)[0]
        if not document_id or not parent or section_id in {"", "unknown"}:
            continue
        groups.setdefault((document_id, parent), {})[section_id] = max(
            groups.get((document_id, parent), {}).get(section_id, 0.0),
            candidate.rerank_score,
        )
    ranked_sections = {
        (
            str(candidate.metadata.get("document_id", "")),
            str(candidate.metadata.get("section_id", "")),
        )
        for candidate in ranked
    }
    inferred = [
        (document_id, parent, max(children.values()))
        for (document_id, parent), children in groups.items()
        if len(children) >= 2 and (document_id, parent) not in ranked_sections
    ]
    return sorted(inferred, key=lambda item: item[2], reverse=True)


def _expand(
    ranked: list[Candidate],
    completeness_requested: bool,
    settings: RAGSettings,
) -> list[Candidate]:
    expanded: dict[str, Candidate] = {candidate.chunk_id: candidate for candidate in ranked}
    if completeness_requested:
        structural_tree_expanded = False
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
            seed = next(
                candidate for candidate in ranked
                if str(candidate.metadata.get("document_id", "")) == document_id
                and str(candidate.metadata.get("section_id", "")) == section_id
            )
            section_has_table = any(
                str(candidate.metadata.get("document_id", "")) == document_id
                and str(candidate.metadata.get("section_id", "")) == section_id
                and str(candidate.metadata.get("chunk_kind", "text")) == "table"
                for candidate in ranked
            )
            # A strong parent heading expands its descendants only when no more
            # specific ranked descendant already identifies the requested leaf.
            # Tables remain section-complete because a split table part must not
            # silently omit rows.
            hierarchical = section_has_table or (
                seed.heading_score >= 0.65
                and not _ranked_descendant_exists(ranked, document_id, section_id)
            )
            records = (
                get_section_tree_chunks(document_id, section_id, settings=settings)
                if hierarchical
                else get_section_chunks(document_id, section_id, settings=settings)
            )
            structural_tree_expanded = structural_tree_expanded or hierarchical or any(
                str(record["metadata"].get("chunk_kind", "text")) == "table"
                for record in records
            )
            for record in records[:settings.max_section_chunks]:
                if record["metadata"].get("is_toc"):
                    continue
                expanded.setdefault(
                    record["id"],
                    _candidate_from_record(
                        record, score * 0.92,
                        "complete_section_tree" if hierarchical else "complete_section",
                    ),
                )
        # Multiple high-ranked sibling clauses are evidence for their shared
        # enumeration parent even when the terse parent heading did not make the
        # dense top-k. Expand at most one such parent to keep context bounded.
        inferred_parents = [] if structural_tree_expanded else _inferred_complete_parents(ranked)[:1]
        for document_id, parent_section, score in inferred_parents:
            records = get_section_tree_chunks(document_id, parent_section, settings=settings)
            for record in records[:settings.max_section_chunks]:
                if record["metadata"].get("is_toc"):
                    continue
                expanded.setdefault(
                    record["id"],
                    _candidate_from_record(record, score * 0.94, "inferred_complete_parent"),
                )
    else:
        # Neighbour evidence is useful around the strongest hits; walking from
        # every reranked candidate compounds weak results into context bloat.
        frontier = list(ranked[:3])
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
        document_id = str(metadata.get("document_id", source))
        domain = metadata_domain(metadata)
        standard_name = str(metadata.get("standard_name", ""))
        standard_version = str(metadata.get("standard_version", ""))
        section = str(metadata.get("section_id", "unknown"))
        numeric_aliases = persian_decimal_aliases(item.text)
        alias_block = ""
        if numeric_aliases:
            alias_block = "\nDeterministic numeric interpretations:\n" + "\n".join(
                f"- {alias['source']} {alias['unit']} = {alias['normalized']} {alias['unit']}"
                for alias in numeric_aliases
            )
        blocks.append(
            f'<SOURCE id="source_{source_index}">\n'
            f"Document: {source}\n"
            f"Document-ID: {document_id}\n"
            f"Domain: {domain}\n"
            f"Standard: {standard_name or 'unspecified'}\n"
            f"Standard-Version: {standard_version or 'unspecified'}\n"
            f"Clause: {clause}\n"
            f"Page: {page}\n"
            f"Section: {section}\n"
            f"Parent-Section: {metadata.get('parent_section_id') or 'none'}\n"
            f"Content-Type: {metadata.get('chunk_kind', 'text')}\n"
            f"Physical-PDF-Page: {metadata.get('pdf_page_number', page)}\n"
            f"Printed-Page: {metadata.get('printed_page_number') or 'unknown'}\n"
            f"Total-PDF-Pages: {metadata.get('total_pdf_pages') or 'unknown'}\n"
            f"Chunk-ID: {item.chunk_id}\n"
            f"Text:\n{item.text}{alias_block}\n"
            f"</SOURCE>"
        )
        source_key = (document_id, clause if clause not in {"", "unknown", "None"} else section, page)
        if page and source_key not in seen_sources:
            seen_sources.add(source_key)
            sources.append({
                "type": "regulation",
                "source": source,
                "clause_id": clause,
                "page_number": page,
                "document_id": document_id,
                "document_domain": domain,
                "standard_name": standard_name,
                "standard_version": standard_version,
                "section_id": section,
                "physical_pdf_page": int(metadata.get("pdf_page_number", page) or page),
                "printed_page_number": int(metadata.get("printed_page_number", 0) or 0) or None,
                "total_pdf_pages": int(metadata.get("total_pdf_pages", 0) or 0) or None,
                "chunk_kind": str(metadata.get("chunk_kind", "text")),
                "table_id": str(metadata.get("table_id", "")),
            })
    return "\n\n".join(blocks), sources, selected


def retrieve_regulations(
    standalone_query: str,
    retrieval_queries: list[str] | None = None,
    *,
    completeness_requested: bool = False,
    document_domains: list[str] | tuple[str, ...] | None = None,
    settings: RAGSettings = SETTINGS,
) -> RegulationRetrieval:
    normalized_query = normalize_retrieval_query(standalone_query)
    surface_query = re.sub(
        r"\s+", " ", normalize_persian_text(standalone_query).casefold(),
    ).strip()
    # Preserve the user's surface form, and add a second dense query only when
    # deterministic typo/synonym correction changed more than case/spacing.
    queries = [standalone_query]
    if normalized_query != surface_query:
        queries.append(normalized_query)
    queries.extend(retrieval_queries or [])
    queries = [value.strip() for value in dict.fromkeys(queries) if value and value.strip()]
    queries = queries[:settings.max_query_variants]
    domains = normalize_domain_filter(document_domains)
    diagnostics = RetrievalDiagnostics(
        retrieval_queries=queries, document_domains=list(domains),
    )
    candidates = _merge_vector_results(queries, settings, domains)
    diagnostics.vector_candidates = len(candidates)
    diagnostics.dense_top = [_candidate_debug(item) for item in sorted(
        candidates, key=lambda item: item.dense_score, reverse=True
    )[:10]]

    # Lexical candidate generation is a normal hybrid stage, not only an
    # emergency fallback. This guarantees that exact headings/codes can enter
    # reranking even when dense retrieval is confidently wrong.
    lexical = _lexical_fallback(normalized_query, settings, domains)
    diagnostics.lexical_candidates = len(lexical)
    diagnostics.lexical_top = [_candidate_debug(item) for item in lexical[:10]]
    candidates = _merge_candidates(candidates, lexical)
    ranked = rerank_candidates(
        standalone_query,
        candidates,
        limit=settings.rerank_count,
        settings=settings,
    )

    # One bounded fallback pass broadens lexical recall if semantic evidence is weak.
    if not ranked or max((item.dense_score for item in candidates), default=0.0) < settings.weak_relevance_threshold:
        fallback = lexical
        diagnostics.fallback_used = True
        candidates = _merge_candidates(candidates, fallback)
        ranked = rerank_candidates(
            standalone_query,
            candidates,
            limit=settings.rerank_count,
            settings=settings,
        )
    diagnostics.reranked_results = len(ranked)
    diagnostics.reranked_top = [_candidate_debug(item) for item in ranked]

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
    diagnostics.selected_chunks = [_candidate_debug(item) for item in selected]
    diagnostics.numeric_aliases = [
        alias for item in selected for alias in persian_decimal_aliases(item.text)
    ]
    logger.info(
        "Regulation retrieval: queries=%d candidates=%d reranked=%d expanded=%d final=%d sections=%d fallback=%s",
        len(queries), diagnostics.vector_candidates, diagnostics.reranked_results,
        diagnostics.expanded_chunks, diagnostics.final_context_chunks,
        diagnostics.final_context_sections, diagnostics.fallback_used,
    )
    logger.info("Regulation context documents/pages: %s", diagnostics.documents_pages)
    return RegulationRetrieval(context, sources, selected, diagnostics)


def _candidate_debug(item: Candidate) -> dict[str, Any]:
    metadata = item.metadata
    return {
        "chunk_id": item.chunk_id,
        "source": metadata.get("source"),
        "page_number": metadata.get("page_number"),
        "printed_page_number": metadata.get("printed_page_number") or None,
        "clause_id": metadata.get("clause_id"),
        "section_id": metadata.get("section_id"),
        "heading": metadata.get("heading"),
        "chunk_kind": metadata.get("chunk_kind", "text"),
        "dense_score": round(float(item.dense_score), 6),
        "lexical_score": round(float(item.lexical_score), 6),
        "heading_score": round(float(item.heading_score), 6),
        "document_score": round(float(item.document_score), 6),
        "rerank_score": round(float(item.rerank_score), 6),
        "query_hits": item.query_hits,
        "expansion_reason": item.expansion_reason,
    }
