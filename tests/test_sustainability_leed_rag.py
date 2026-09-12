from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import routes
from main import app
from rag.chunker import Chunk
from rag.config import RAGSettings
from rag.document_metadata import classify_document, metadata_domain
from rag.embedder import _metadata
from rag.memory import ConversationStore
from rag.orchestrator import (
    RAGOrchestrator,
    apply_routing_policy,
    canonicalize_generated_citations,
    validate_generated_answer,
    validate_leed_assessment_language,
    validate_sustainability_numeric_claims,
)
from rag.reranker import Candidate
from rag.retrieval import RegulationRetrieval, RetrievalDiagnostics, assemble_context, retrieve_regulations
from sustainability.assessment import assess_available_evidence
from sustainability.retriever import SustainabilityEvidence, SustainabilityEvidenceRetriever


def sustainability_evidence(value: float = 250.0) -> SustainabilityEvidence:
    data = {
        "scope": {"project_id": "p", "file_ids": ["f"], "run_id": "run-1"},
        "totals": {"calculated_carbon_kgco2e": value},
    }
    return SustainabilityEvidence(
        context=(
            '<SUSTAINABILITY_EVIDENCE deterministic="true">\n'
            f'{{"calculated_carbon_kgco2e": {value}}}\n'
            "</SUSTAINABILITY_EVIDENCE>"
        ),
        source={
            "type": "sustainability", "id": "run-1", "name": "Deterministic sustainability analysis",
            "project_id": "p", "file_ids": ["f"],
        },
        data=data,
        debug={"status": "available", "intent": "summary", "run_id": "run-1"},
        carbon_values={format(value, ".15g")},
        display_text=f"Calculated embodied carbon: {value} kgCO2e.",
    )


class FakeSustainabilityRetriever:
    def __init__(self, evidence: SustainabilityEvidence):
        self.evidence = evidence
        self.calls = []

    def retrieve(self, question, **scope):
        self.calls.append((question, scope))
        return self.evidence


def leed_retrieval() -> RegulationRetrieval:
    candidate = Candidate(
        "leed-p12-c1",
        "Projects should document responsibly sourced construction products.",
        {
            "document_id": "leed-v4-1", "document_domain": "leed",
            "standard_name": "LEED", "standard_version": "v4.1",
            "source": "LEED-v4.1.pdf", "page_number": 12,
            "clause_id": "unknown", "section_id": "MR-credit-products",
            "chunk_index": 1,
        },
        rerank_score=0.9,
    )
    context, sources, chunks = assemble_context([candidate])
    return RegulationRetrieval(context, sources, chunks, RetrievalDiagnostics())


def test_document_classification_is_backward_compatible_and_versioned():
    assert classify_document().document_domain == "regulation"
    leed = classify_document("LEED", standard_version="v4.1")
    assert leed.standard_name == "LEED"
    with pytest.raises(ValueError, match="standard_version"):
        classify_document("leed")
    with pytest.raises(ValueError, match="Unsupported"):
        classify_document("marketing")

    stored = _metadata(Chunk(
        "c", "material requirements", 12, None,
        document_id="leed-v4-1", document_domain="leed",
        standard_name="LEED", standard_version="v4.1", section_id="MR",
    ))
    assert stored["document_domain"] == "leed"
    assert stored["standard_version"] == "v4.1"
    assert metadata_domain({}) == "regulation"  # legacy chunk behavior


def test_leed_retrieval_applies_domain_filter_to_dense_and_final_candidates(monkeypatch):
    captured = []

    def fake_query(_query, **kwargs):
        captured.append(tuple(kwargs["document_domains"]))
        return {
            "ids": [["leed", "reg"]],
            "documents": [["LEED material guidance", "ordinary fire regulation"]],
            "metadatas": [[
                {"document_id": "l", "document_domain": "leed", "source": "leed.pdf",
                 "page_number": 5, "clause_id": "unknown", "section_id": "MR"},
                {"document_id": "r", "document_domain": "regulation", "source": "fire.pdf",
                 "page_number": 8, "clause_id": "4-1-1", "section_id": "4-1-1"},
            ]],
            "distances": [[0.05, 0.01]],
        }

    monkeypatch.setattr("rag.retrieval.query_similar", fake_query)
    result = retrieve_regulations(
        "LEED construction materials", document_domains=["leed", "sustainability", "standard"],
        settings=RAGSettings(weak_relevance_threshold=0.0, rerank_count=5),
    )
    assert captured == [("leed", "sustainability", "standard")]
    assert [item.chunk_id for item in result.chunks] == ["leed"]
    assert result.diagnostics.document_domains == ["leed", "sustainability", "standard"]


def test_unlabelled_leed_section_has_grounded_document_citation():
    retrieval = leed_retrieval()
    valid = "Guidance is available. [Document leed-v4-1, Section MR-credit-products, Page 12]"
    invalid = "Invented. [Document leed-v4-1, Section MR-credit-products, Page 99]"
    assert validate_generated_answer(valid, retrieval.sources, True)
    assert not validate_generated_answer(invalid, retrieval.sources, True)
    assert canonicalize_generated_citations(valid) == valid


@pytest.mark.parametrize("question", [
    "What is the estimated embodied carbon of this project?",
    "بیشترین سهم کربن مربوط به کدام مصالح است؟",
])
def test_sustainability_routing_guard_is_bilingual(question):
    routed = apply_routing_policy(question, {
        "needs_vector": False, "needs_graph": False, "needs_sustainability": False,
        "needs_leed_assessment": False, "document_domains": [],
        "is_technical": True,
    })
    assert routed["needs_sustainability"] is True


def test_semantic_router_can_select_sustainability_without_keyword_override():
    routed = apply_routing_policy("Compare the available project evidence.", {
        "needs_vector": False, "needs_graph": False, "needs_sustainability": True,
        "needs_leed_assessment": False, "document_domains": [],
        "sustainability_intent": "summary", "is_technical": True,
    })
    assert routed["needs_sustainability"] is True
    assert routed["needs_vector"] is False

    documentary = apply_routing_policy("What are sustainable materials?", {
        "needs_vector": False, "needs_graph": False, "needs_sustainability": False,
        "needs_leed_assessment": False, "document_domains": [], "is_technical": True,
    })
    assert documentary["needs_vector"] is True
    assert documentary["needs_sustainability"] is False
    assert set(documentary["document_domains"]) == {"leed", "sustainability", "standard"}

    assessment = apply_routing_policy(
        "آیا اطلاعات موجود در مدل برای بررسی این معیار LEED کافی است؟",
        {
            "needs_vector": False, "needs_graph": False, "needs_sustainability": False,
            "needs_leed_assessment": False, "document_domains": [], "is_technical": True,
        },
    )
    assert assessment["needs_vector"] is True
    assert assessment["needs_sustainability"] is True
    assert assessment["needs_leed_assessment"] is True

    cross_domain = apply_routing_policy("Compare LEED with the building regulations", {
        "needs_vector": True, "needs_graph": False, "needs_sustainability": False,
        "needs_leed_assessment": False, "document_domains": [], "is_technical": True,
    })
    assert set(cross_domain["document_domains"]) == {
        "regulation", "leed", "sustainability", "standard",
    }


def test_hybrid_retrieval_keeps_deterministic_and_document_evidence_separate(monkeypatch):
    fake_sustainability = FakeSustainabilityRetriever(sustainability_evidence())
    orchestrator = RAGOrchestrator(
        graph_retriever=object(), sustainability_retriever=fake_sustainability,
    )
    monkeypatch.setattr("rag.orchestrator.retrieve_regulations", lambda *args, **kwargs: leed_retrieval())
    result = orchestrator.retrieve({
        "standalone_query": "contributors and LEED guidance",
        "retrieval_queries": [], "needs_vector": True, "needs_graph": False,
        "needs_sustainability": True, "needs_leed_assessment": True,
        "document_domains": ["leed", "sustainability", "standard"],
        "sustainability_intent": "leed_assessment", "project_id": "p", "file_ids": ["f"],
    })
    assert "250.0" in result.sustainability_context
    assert "LEED-v4.1.pdf" in result.vector_context
    assert result.assessment.status == "not_automatically_evaluable"
    assert {source["type"] for source in result.sources} == {"regulation", "sustainability"}

    monkeypatch.setattr(orchestrator, "understand", lambda question, state, profile: {
        "standalone_query": question, "retrieval_queries": [], "needs_vector": True,
        "needs_graph": False, "needs_sustainability": True, "needs_leed_assessment": True,
        "document_domains": ["leed", "sustainability", "standard"],
        "sustainability_intent": "leed_assessment", "is_technical": True,
        "intent": "technical", "is_follow_up": False, "completeness_requested": False,
        "topic": "carbon and LEED",
    })
    monkeypatch.setattr(
        orchestrator, "generate",
        lambda *_args, **_kwargs: (
            "The project calculated carbon is 250 kgCO2e. "
            "Relevant product guidance is available. "
            "[Document leed-v4-1, Section MR-credit-products, Page 12] "
            "Assessment: not_automatically_evaluable."
        ),
    )
    answer = orchestrator.ask("contributors and LEED guidance", project_id="p", file_ids=["f"])
    assert answer["used_vector"] is True
    assert answer["used_sustainability"] is True
    assert answer["assessment"]["status"] == "not_automatically_evaluable"
    assert {source["type"] for source in answer["sources"]} == {"regulation", "sustainability"}
    findings = orchestrator.memory.get_sustainability_findings(
        answer["conversation_id"], project_id="p", file_ids=["f"],
    )
    assert findings[0]["criterion"] == "contributors and LEED guidance"
    assert findings[0]["document_citations"][0]["document_id"] == "leed-v4-1"


def test_assessment_distinguishes_missing_leed_and_missing_bim_evidence(monkeypatch):
    available = FakeSustainabilityRetriever(sustainability_evidence())
    orchestrator = RAGOrchestrator(graph_retriever=object(), sustainability_retriever=available)
    monkeypatch.setattr(
        "rag.orchestrator.retrieve_regulations",
        lambda *args, **kwargs: RegulationRetrieval(),
    )
    missing_leed = orchestrator.retrieve({
        "standalone_query": "evaluate", "retrieval_queries": [], "needs_vector": True,
        "needs_graph": False, "needs_sustainability": True, "needs_leed_assessment": True,
        "document_domains": ["leed"], "project_id": "p", "file_ids": ["f"],
    })
    assert missing_leed.assessment.status == "insufficient_evidence"
    assert missing_leed.assessment.has_bim_sustainability_evidence is True
    assert missing_leed.assessment.has_document_evidence is False

    unavailable = SustainabilityEvidence(debug={"status": "missing_scope", "code": "project_scope_required"})
    orchestrator.sustainability_retriever = FakeSustainabilityRetriever(unavailable)
    monkeypatch.setattr("rag.orchestrator.retrieve_regulations", lambda *args, **kwargs: leed_retrieval())
    missing_bim = orchestrator.retrieve({
        "standalone_query": "evaluate", "retrieval_queries": [], "needs_vector": True,
        "needs_graph": False, "needs_sustainability": True, "needs_leed_assessment": True,
        "document_domains": ["leed"], "project_id": None, "file_ids": None,
    })
    assert missing_bim.assessment.status == "insufficient_evidence"
    assert missing_bim.assessment.has_bim_sustainability_evidence is False
    assert missing_bim.assessment.has_document_evidence is True


def test_assessment_positive_and_negative_states_require_named_deterministic_evaluator():
    unsupported = assess_available_evidence(
        has_bim_sustainability_evidence=True, has_document_evidence=True,
    )
    positive = assess_available_evidence(
        has_bim_sustainability_evidence=True, has_document_evidence=True,
        evaluator_id="reviewed-rule-1", deterministic_outcome=True,
    )
    negative = assess_available_evidence(
        has_bim_sustainability_evidence=True, has_document_evidence=True,
        evaluator_id="reviewed-rule-1", deterministic_outcome=False,
    )
    assert unsupported.status == "not_automatically_evaluable"
    assert positive.status == "satisfied_from_available_evidence"
    assert negative.status == "not_satisfied_from_available_evidence"
    assert validate_leed_assessment_language(
        "Status: not_automatically_evaluable", unsupported,
    )
    assert not validate_leed_assessment_language(
        "Status: satisfied_from_available_evidence", unsupported,
    )
    assert not validate_leed_assessment_language(
        "The project is certified as LEED Gold.", unsupported,
    )


def test_invented_carbon_value_is_rejected_and_ask_falls_back_to_evidence(monkeypatch):
    assert validate_sustainability_numeric_claims(
        "Embodied carbon is 250 kgCO2e.", '{"calculated_carbon_kgco2e": 250}'
    )
    assert not validate_sustainability_numeric_claims(
        "Embodied carbon is 999 kgCO2e.", '{"calculated_carbon_kgco2e": 250}'
    )
    assert not validate_sustainability_numeric_claims(
        "The project total is 999 kgCO2e. [Document leed, Section MR, Page 2]",
        '{"calculated_carbon_kgco2e": 250}',
        '<SOURCE>Page: 2\nText: a documentary threshold of 999 kgCO2e</SOURCE>',
    )
    assert validate_sustainability_numeric_claims(
        "The requirement states 999 kgCO2e. [Document leed, Section MR, Page 2]",
        '{"calculated_carbon_kgco2e": 250}',
        '<SOURCE>Page: 2\nText: a documentary threshold of 999 kgCO2e</SOURCE>',
    )

    evidence = sustainability_evidence()
    orchestrator = RAGOrchestrator(
        memory=ConversationStore(RAGSettings()), graph_retriever=object(),
        sustainability_retriever=FakeSustainabilityRetriever(evidence),
    )
    monkeypatch.setattr(orchestrator, "understand", lambda question, state, profile: {
        "standalone_query": question, "retrieval_queries": [], "needs_vector": False,
        "needs_graph": False, "needs_sustainability": True, "needs_leed_assessment": False,
        "document_domains": [], "sustainability_intent": "summary", "is_technical": True,
        "intent": "technical", "is_follow_up": False, "completeness_requested": False,
        "topic": "embodied carbon",
    })
    monkeypatch.setattr(
        orchestrator, "generate",
        lambda *_args, **_kwargs: "The embodied carbon is 999 kgCO2e.",
    )
    answer = orchestrator.ask("What is the project embodied carbon?", project_id="p", file_ids=["f"])
    assert "999" not in answer["answer"]
    assert "250.0 kgCO2e" in answer["answer"]


def test_standard_and_strong_modes_use_identical_deterministic_evidence(monkeypatch):
    evidence = sustainability_evidence(321.5)
    fake = FakeSustainabilityRetriever(evidence)
    settings = RAGSettings(
        router_model="standard-router", final_model="standard-final",
        strong_router_model="strong-router", strong_final_model="strong-final",
    )
    orchestrator = RAGOrchestrator(
        settings=settings, memory=ConversationStore(settings), graph_retriever=object(),
        sustainability_retriever=fake,
    )
    monkeypatch.setattr(orchestrator, "understand", lambda question, state, profile: {
        "standalone_query": question, "retrieval_queries": [], "needs_vector": False,
        "needs_graph": False, "needs_sustainability": True, "needs_leed_assessment": False,
        "document_domains": [], "sustainability_intent": "summary", "is_technical": True,
        "intent": "technical", "is_follow_up": False, "completeness_requested": False,
        "topic": "carbon",
    })
    monkeypatch.setattr(
        orchestrator, "generate",
        lambda _q, _u, retrieval, _state, _profile: retrieval.sustainability.display_text,
    )
    standard = orchestrator.ask("carbon", "standard", project_id="p", file_ids=["f"])
    strong = orchestrator.ask("carbon", "strong", True, project_id="p", file_ids=["f"])
    assert standard["model_mode"] == "standard"
    assert strong["model_mode"] == "strong"
    assert standard["answer"] == strong["answer"] == "Calculated embodied carbon: 321.5 kgCO2e."
    assert standard["sources"] == strong["sources"]


def test_api_accepts_scope_and_document_upload_classification(monkeypatch):
    request = routes.QuestionRequest(question="carbon", project_id="p", file_ids=["f"])
    assert request.project_id == "p" and request.file_ids == ["f"]
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    assert "payload.project_id = activeProjectId" in javascript
    assert "payload.file_ids = Array.from(selectedIfcFileIds)" in javascript

    captured = {}
    monkeypatch.setattr(routes, "_ensure_rag", lambda: None)

    def chunker(_path, **kwargs):
        captured.update(kwargs)
        return [SimpleNamespace(chunk_id="c")]

    monkeypatch.setattr(routes, "_RAG_CHUNKER", chunker)
    monkeypatch.setattr(routes, "_RAG_EMBEDDER", {
        "delete_document": lambda _doc_id: 0,
        "embed_and_store": lambda chunks: len(chunks),
        "CHROMA_DIR": "test", "COLLECTION_NAME": "test",
    })
    response = TestClient(app).post(
        "/api/rag/upload?document_domain=leed&standard_version=v4.1",
        files=[("files", ("leed.pdf", b"pdf", "application/pdf"))],
    )
    assert response.status_code == 200
    assert captured["document_domain"] == "leed"
    assert captured["standard_name"] == "LEED"
    assert response.json()["files"][0]["standard_version"] == "v4.1"
