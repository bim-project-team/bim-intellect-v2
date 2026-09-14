"""Tests for the visualization payload attached to every answer.

The concern here is honesty of the contract: an answer that was withheld, a
graph that failed, and a graph that legitimately found nothing are three
different claims and must not collapse into one empty highlight list.

No LLM or database is used: understanding and generation are stubbed, and the
graph retriever is a fake, so these assert orchestration rather than model output.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bim_graph.scene_export import scene_key
from bim_graph.visualization import (
    REASON_GRAPH_ELEMENTS, REASON_GRAPH_UNAVAILABLE, REASON_NO_EVIDENCE,
    REASON_RELATED_TYPES,
)
from rag.document_metadata import DocumentPageCountAnswer
from rag.orchestrator import RAGOrchestrator

ELEMENTS = [
    {"element_id": "p::f::A", "ifc_guid": "guid-a", "name": "Wall A",
     "ifc_type": "IfcWall", "storey_name": "Level 5"},
    {"element_id": "p::f::B", "ifc_guid": "guid-b", "name": "Pipe B",
     "ifc_type": "IfcFlowSegment", "storey_name": "Level 5"},
]

GRAPH_RESULT = {
    "cypher_query": "MATCH (a)-[r:CLASHES_WITH]->(b) RETURN count(r) AS issue_count",
    "cypher_source": "planner", "record_count": 1, "graph_intent": "issues_by_type",
    "requested_outputs": [], "returned_columns": ["issue_count"],
    "completeness_valid": True, "missing_outputs": [], "query_count": 2,
    "follow_up_query_required": False,
    "context": "Building Graph Results (1 total):\n  1. issue_type=CLASH | issue_count=6",
    "sources": [], "elements": ELEMENTS,
}

TECHNICAL = {
    "standalone_query": "clashes on level 5", "retrieval_queries": [],
    "needs_vector": False, "needs_graph": True, "is_technical": True,
    "intent": "technical", "is_follow_up": False, "completeness_requested": False,
    "topic": "clashes",
}
REGULATION_ONLY = {
    **TECHNICAL,
    "standalone_query": "minimum landing depth for stairs",
    "needs_vector": False, "needs_graph": True, "topic": "stairs",
}
CONVERSATION = {
    "standalone_query": "سلام", "retrieval_queries": [], "needs_vector": False,
    "needs_graph": False, "is_technical": False, "intent": "conversation",
    "is_follow_up": False, "completeness_requested": False, "topic": None,
}


class _FakeGraph:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def ask(self, _question):
        if self.error:
            raise RuntimeError(self.error)
        return self.result


def _ask(graph, understanding, *, answer="در طبقه ۵ شش تداخل ثبت شده است."):
    orchestrator = RAGOrchestrator(graph_retriever=graph)
    with patch.object(RAGOrchestrator, "understand", return_value=understanding), \
         patch.object(RAGOrchestrator, "generate", return_value=answer), \
         patch.object(RAGOrchestrator, "_generate_conversation", return_value="سلام!"):
        return orchestrator.ask("پرسش", project_id="demo")


def test_every_answer_carries_a_visualization_block():
    """The frontend must never have to branch on the key's existence."""
    for graph, understanding in (
        (_FakeGraph(GRAPH_RESULT), TECHNICAL),
        (_FakeGraph(GRAPH_RESULT), CONVERSATION),
        (_FakeGraph(error="bolt refused"), TECHNICAL),
    ):
        payload = _ask(graph, understanding)["visualization"]
        assert set(payload) == {
            "available", "reason", "project_id", "file_ids", "highlight", "scenes",
            "related_types", "truncated",
        }


def test_metadata_only_answer_keeps_the_visualization_contract():
    metadata = DocumentPageCountAnswer(
        "مبحث ۲۴ در مجموع ۲۳۶ صفحه دارد.",
        {"type": "document_metadata", "document_id": "mabhas-24", "total_pdf_pages": 236},
    )
    with patch("rag.orchestrator.list_indexed_documents", return_value=[]), \
         patch("rag.orchestrator.resolve_document_page_count", return_value=metadata):
        result = _ask(_FakeGraph(GRAPH_RESULT), TECHNICAL)

    assert result["retrieval_debug"]["intent"] == "document_metadata"
    assert result["visualization"]["available"] is False
    assert result["visualization"]["highlight"] == []


def test_graph_elements_are_offered_with_their_storey_scene():
    payload = _ask(_FakeGraph(GRAPH_RESULT), TECHNICAL)["visualization"]
    assert payload["available"] is True
    assert payload["reason"] == REASON_GRAPH_ELEMENTS
    assert [item["ifc_guid"] for item in payload["highlight"]] == ["guid-a", "guid-b"]
    assert payload["scenes"] == [scene_key("Level 5")]
    assert payload["project_id"] == "demo"


def test_regulation_only_answer_offers_the_opt_in_type_view():
    """No elements were verified, so this is an offer rather than evidence."""
    graph = _FakeGraph({**GRAPH_RESULT, "elements": []})
    payload = _ask(graph, REGULATION_ONLY)["visualization"]
    assert payload["available"] is False
    assert payload["reason"] == REASON_RELATED_TYPES
    assert payload["related_types"] == ["IfcStair", "IfcStairFlight"]


def test_conversational_turn_offers_nothing():
    payload = _ask(_FakeGraph(GRAPH_RESULT), CONVERSATION)["visualization"]
    assert payload["reason"] == REASON_NO_EVIDENCE
    assert payload["highlight"] == [] and payload["related_types"] == []


def test_graph_failure_is_reported_as_a_failure_not_as_absence():
    payload = _ask(_FakeGraph(error="bolt refused"), TECHNICAL)["visualization"]
    assert payload["reason"] == REASON_GRAPH_UNAVAILABLE
    assert payload["available"] is False
    # Falling back to the type view here would dress a failure up as an answer.
    assert payload["related_types"] == []


def test_withheld_answer_highlights_nothing():
    """When citation validation rejects the answer, nothing was asserted.

    Highlighting elements would imply the system verified a claim it explicitly
    refused to make.
    """
    graph = _FakeGraph(GRAPH_RESULT)
    orchestrator = RAGOrchestrator(graph_retriever=graph)
    with patch.object(RAGOrchestrator, "understand", return_value={
             **TECHNICAL, "needs_vector": True,
         }), \
         patch.object(RAGOrchestrator, "retrieve") as retrieve, \
         patch.object(RAGOrchestrator, "generate", return_value="ادعای بدون ارجاع"), \
         patch.object(RAGOrchestrator, "_repair_citations", return_value="هنوز بدون ارجاع"):
        from rag.orchestrator import RetrievalResult
        retrieve.return_value = RetrievalResult(
            vector_context="<SOURCE>...</SOURCE>",
            graph_context="Building Graph Results",
            sources=[{"type": "regulation", "clause_id": "15-2-1", "page_number": 12}],
            graph_elements=ELEMENTS,
        )
        result = orchestrator.ask("پرسش", project_id="demo")

    assert result["sources"] == []
    assert result["visualization"]["available"] is False
    assert result["visualization"]["highlight"] == []


def test_project_scope_is_absent_when_the_caller_gives_none():
    """Without a project the viewer cannot resolve scenes, and says so."""
    orchestrator = RAGOrchestrator(graph_retriever=_FakeGraph(GRAPH_RESULT))
    with patch.object(RAGOrchestrator, "understand", return_value=TECHNICAL), \
         patch.object(RAGOrchestrator, "generate", return_value="پاسخ"):
        payload = orchestrator.ask("پرسش")["visualization"]
    assert payload["project_id"] is None
    assert payload["available"] is True


def test_visualization_preserves_the_answer_file_scope():
    orchestrator = RAGOrchestrator(graph_retriever=_FakeGraph(GRAPH_RESULT))
    with patch.object(RAGOrchestrator, "understand", return_value=TECHNICAL), \
         patch.object(RAGOrchestrator, "generate", return_value="پاسخ"):
        payload = orchestrator.ask(
            "پرسش", project_id="demo", file_ids=["architectural", "mep"],
        )["visualization"]
    assert payload["file_ids"] == ["architectural", "mep"]


def test_visualization_never_pollutes_the_citation_sources():
    """`sources` drives citation chips and is filtered against the answer text.

    Element highlights have a different lifecycle, so they must stay out of it.
    """
    result = _ask(_FakeGraph(GRAPH_RESULT), TECHNICAL)
    assert all(source.get("type") != "visualization" for source in result["sources"])
    assert result["visualization"]["highlight"] is not result["sources"]
