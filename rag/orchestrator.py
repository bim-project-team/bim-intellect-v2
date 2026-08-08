"""Orchestrates Vector, Graph, or Hybrid retrieval for BIM-Intellect RAG.

Pipeline:
    1. Route   → LLM decides if vector / graph / both are needed.
    2. Retrieve → Fetch raw chunks from Chroma + raw records from Neo4j.
    3. Generate → Single LLM call with combined context.

Reuses the existing OpenRouter client (call_with_retries) and embedder
(query_similar) so no new API keys or DB connections are required.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .embedder import query_similar
from .openrouter_client import (
    CHAT_MODEL,
    LLMConfigError,
    LLMRequestError,
    call_with_retries,
    get_client,
)

from .prompts import ROUTER_PROMPT, COMBINE_PROMPT
from bim_graph.graph_retriever import GraphRetriever

logger = logging.getLogger("bim_intellect.orchestrator")


@dataclass
class RetrievalResult:
    vector_context: Optional[str] = None
    graph_context: Optional[str] = None
    sources: list = field(default_factory=list)

    @property
    def has_any_context(self) -> bool:
        return bool(self.vector_context or self.graph_context)

def valid_source(source: dict) -> bool:
    clause_id = str(source.get("clause_id", "")).strip().lower()
    page_number = source.get("page_number")

    return (
        clause_id not in {"", "unknown", "none", "null"}
        and page_number is not None
    )



def deduplicate_sources(sources: list[dict]) -> list[dict]:
    seen = set()
    result = []

    for source in sources:
        source_type = source.get("type", "regulation")

        if source_type == "regulation":
            key = (
                "regulation",
                source.get("clause_id"),
                source.get("page_number"),
            )
        else:
            key = (
                source_type,
                source.get("element_id") or source.get("id"),
                source.get("name"),
            )

        if key not in seen:
            seen.add(key)
            result.append(source)

    return result



def _chat_callable(messages, model, temperature):
    """Factory for a zero-arg callable compatible with call_with_retries."""
    def _call():
        client = get_client()
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
    return _call


def _format_vector_results(results: dict) -> tuple[str, list[dict]]:
    """Turn Chroma results into citation-tagged context and valid metadata."""
    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]

    if not docs or not docs[0]:
        return "", []

    blocks = []
    sources = []

    for doc, meta in zip(docs[0], metas[0]):
        meta = meta or {}

        clause_id = str(meta.get("clause_id", "")).strip()
        page_number = meta.get("page_number")

        # Never expose unverifiable regulatory citations.
        if not clause_id or clause_id.lower() in {
            "unknown",
            "none",
            "null",
        }:
            logger.warning(
                "Skipping chunk with invalid clause_id: %r",
                meta,
            )
            continue

        if page_number is None:
            logger.warning(
                "Skipping chunk with missing page_number: %r",
                meta,
            )
            continue

        blocks.append(
            f"[Clause {clause_id}, Page {page_number}] {doc}"
        )

        sources.append({
            "type": "regulation",
            "source": meta.get("source", "Mabhas 15"),
            "clause_id": clause_id,
            "page_number": page_number,
        })

    return "\n\n".join(blocks), deduplicate_sources(sources)

def filter_sources(sources: list[dict]) -> list[dict]:
    valid = []

    for source in sources:
        source_type = source.get("type", "regulation")

        if source_type == "regulation":
            if valid_source(source):
                valid.append(source)
        else:
            # Preserve graph/element citations for BIM traceability.
            valid.append(source)

    return deduplicate_sources(valid)
CITATION_PATTERN = re.compile(
    r"(?:\[)?Clause\s+([0-9]+(?:-[0-9]+)+)"
    r"\s*,?\s*Page\s+([0-9]+)(?:\])?",
    re.IGNORECASE,
)


def cited_regulation_pairs(answer: str) -> set[tuple[str, str]]:
    return {
        (clause_id.strip(), page_number.strip())
        for clause_id, page_number in CITATION_PATTERN.findall(answer or "")
    }


def supported_regulation_pairs(
    sources: list[dict],
) -> set[tuple[str, str]]:
    return {
        (
            str(source.get("clause_id", "")).strip(),
            str(source.get("page_number", "")).strip(),
        )
        for source in sources
        if source.get("type", "regulation") == "regulation"
        and valid_source(source)
    }


def validate_generated_answer(
    answer: str,
    sources: list[dict],
    requires_regulatory_citation: bool = False,
) -> bool:
    cited = cited_regulation_pairs(answer)
    supported = supported_regulation_pairs(sources)

    if cited - supported:
        logger.warning(
            "Answer contains unsupported citations: %s",
            sorted(cited - supported),
        )
        return False

    if requires_regulatory_citation and not cited:
        logger.warning("Regulatory answer contains no clause/page citation.")
        return False

    return True

def sources_used_by_answer(
    answer: str,
    sources: list[dict],
) -> list[dict]:
    cited = cited_regulation_pairs(answer)

    if not cited:
        return []

    return [
        source
        for source in sources
        if source.get("type", "regulation") != "regulation"
        or (
            str(source.get("clause_id", "")).strip(),
            str(source.get("page_number", "")).strip(),
        ) in cited
    ]

class RAGOrchestrator:
    """End-to-end RAG with automatic routing between vector and graph sources."""

    def __init__(self, n_results: int = 5):
        self.n_results = n_results
        self.graph_retriever = GraphRetriever()

    def route(self, question: str) -> dict:
        """LLM decides which sources to query. Returns dict with needs_vector, needs_graph."""
        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": question}
        ]
        try:
            response = call_with_retries(
                _chat_callable(messages, CHAT_MODEL, 0.0),
                op_name="query router"
            )
            content = response.choices[0].message.content.strip()
            decision = json.loads(content)
        except (json.JSONDecodeError, LLMRequestError, LLMConfigError) as exc:
            logger.warning("Router failed (%s) — falling back to both sources.", exc)
            decision = {
                "needs_vector": True,
                "needs_graph": True,
                "reasoning": f"Fallback due to error: {exc}"
            }

        decision.setdefault("needs_vector", True)
        decision.setdefault("needs_graph", True)
        return decision

    def retrieve(self, question: str) -> RetrievalResult:
        """Retrieve from selected sources based on routing decision."""
        decision = self.route(question)
        result = RetrievalResult()

        logger.info(
            "Router decision: vector=%s graph=%s | %s",
            decision["needs_vector"], decision["needs_graph"],
            decision.get("reasoning", "")
        )

        # 2a) Vector / Regulation path
        if decision.get("needs_vector"):
            try:
                chroma_results = query_similar(question, n_results=self.n_results)
                context, sources = _format_vector_results(chroma_results)
                if context:
                    result.vector_context = context
                    result.sources.extend(sources)
                else:
                    logger.info("Vector retrieval returned no chunks.")
            except Exception as exc:
                logger.error("Vector retrieval failed: %s", exc)

        # 2b) Graph / Neo4j path
        if decision.get("needs_graph"):
            try:
                graph_answer = self.graph_retriever.ask(question)
                if graph_answer.get("context"):
                    cypher = graph_answer.get("cypher_query", "")
                    result.graph_context = (
                        f"Query executed: {cypher}\n\n{graph_answer['context']}"
                    )
                    result.sources.extend(graph_answer.get("sources", []))
                else:
                    logger.info("Graph retrieval returned no data.")
            except Exception as exc:
                logger.error("Graph retrieval failed: %s", exc)

        return result

    def generate(self, question: str, retrieval: RetrievalResult) -> str:
        """Combine contexts and generate final answer via single LLM call."""
        if not retrieval.has_any_context:
            return (
                "I couldn't find relevant information in the regulations or "
                "building data to answer this question. Try rephrasing or "
                "confirm that both the regulation corpus and the building graph "
                "have been ingested."
            )

        prompt = COMBINE_PROMPT.format(
            vector_context=(
                retrieval.vector_context
                or "No regulatory context retrieved."
            ),
            graph_context=(
                retrieval.graph_context
                or "No building graph context retrieved."
            ),
            question=question,
        )

        messages = [{"role": "user", "content": prompt}]

        try:
            response = call_with_retries(
                _chat_callable(messages, CHAT_MODEL, 0.0),
                op_name="final answer generation",
            )
            return response.choices[0].message.content or ""
        except (LLMRequestError, LLMConfigError) as exc:
            logger.error("Final generation failed: %s", exc)
            raise

    def ask(self, question: str) -> dict:
        """Full pipeline: Route → Retrieve → Generate."""
        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        retrieval = self.retrieve(question)
        retrieval.sources = filter_sources(retrieval.sources)

        answer = self.generate(question, retrieval)

        requires_regulatory_citation = (
            retrieval.vector_context is not None
        )

        if not validate_generated_answer(
            answer,
            retrieval.sources,
            requires_regulatory_citation=requires_regulatory_citation,
        ):
            answer = (
                "Insufficient evidence. The retrieved regulation context "
                "does not support a traceable answer with valid clause IDs "
                "and page numbers."
            )
            answer_sources = []
        else:
            answer_sources = sources_used_by_answer(
                answer,
                retrieval.sources,
            )

        return {
            "answer": answer,
            "sources": answer_sources,
            "used_vector": retrieval.vector_context is not None,
            "used_graph": retrieval.graph_context is not None,
        }
