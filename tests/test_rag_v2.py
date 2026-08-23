import re
from pathlib import Path

import numpy as np
import pytest

from api.routes import QuestionRequest
from rag.chunker import chunk_pdf_with_diagnostics
from rag.config import RAGSettings, get_model_profile
from rag.embedder import MultilingualEmbeddingFunction, collection_status
from rag.memory import ConversationState, ConversationStore
from rag.orchestrator import _safe_understanding_fallback, apply_routing_policy
from rag.reranker import Candidate, rerank_candidates
from rag.retrieval import _expand, assemble_context, retrieve_regulations


TARGET_PDF = "dataset/sources/22 - v1-1392 - مراقبت و نگهداری.pdf"


def test_api_request_is_backward_compatible_and_supports_model_mode():
    legacy = QuestionRequest(question="سلام")
    assert legacy.conversation_id is None
    assert legacy.use_strong_models is False
    enhanced = QuestionRequest(question="ادامه", conversation_id="chat-1", use_strong_models=True)
    assert enhanced.conversation_id == "chat-1"
    assert enhanced.use_strong_models is True


def test_chat_ui_toggle_defaults_off_and_sends_session_fields():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    toggle = re.search(r'<input type="checkbox" id="strong-models-toggle"([^>]*)>', html)
    assert toggle and "checked" not in toggle.group(1)
    assert "use_strong_models" in javascript
    assert "conversation_id" in javascript
    assert "sessionStorage" in javascript


def test_strong_model_profile_uses_gemini_then_claude():
    settings = RAGSettings(
        strong_router_model="google/gemini-test",
        strong_final_model="anthropic/claude-sonnet-test",
    )
    standard = get_model_profile(False, settings)
    strong = get_model_profile(True, settings)
    assert standard.mode == "standard"
    assert strong.router_model == "google/gemini-test"
    assert strong.final_model == "anthropic/claude-sonnet-test"


def test_target_electrical_visual_inspection_is_one_expandable_section():
    result = chunk_pdf_with_diagnostics(TARGET_PDF, doc_id="mabhas22")
    chunks = [chunk for chunk in result.chunks if chunk.clause_id == "22-7-7" and not chunk.is_toc]
    assert len(chunks) == 3
    assert {chunk.page_number for chunk in chunks} == {67, 68}
    assert chunks[0].next_section_chunk_id == chunks[1].chunk_id
    assert chunks[1].next_section_chunk_id == chunks[2].chunk_id
    text = "\n".join(chunk.text for chunk in chunks)
    # The PDF contains 15 explicit lettered requirements in this clause.
    markers = ["الف-", "ب-", "پ-", "ت-", "ث -", "ج-", "چ-", "ح -", "خ -",
               "د -", "ذ-", "ر -", "ز -", "ژ-", "س-"]
    assert all(marker in text for marker in markers)


@pytest.mark.parametrize("follow_up", [
    "همین؟", "بازم بگو", "کاملش کن",
    "کاملش کن و همه مواردی که در منبع اومده رو بگو",
])
def test_follow_up_fallback_inherits_topic_and_requests_complete_vector_search(follow_up):
    state = ConversationState("chat", topic="بازدید عینی از تاسیسات برقی")
    decision = _safe_understanding_fallback(follow_up, state, RuntimeError("offline"))
    assert decision["needs_vector"] is True
    assert decision["needs_graph"] is False
    assert decision["is_follow_up"] is True
    assert decision["completeness_requested"] is True
    assert "بازدید عینی از تاسیسات برقی" in decision["standalone_query"]


@pytest.mark.parametrize("message", ["سلام", "ممنون", "فهمیدم", "خیلی خوب بود"])
def test_normal_conversation_never_becomes_insufficient_evidence(message):
    decision = _safe_understanding_fallback(message, ConversationState("chat"), RuntimeError("offline"))
    assert decision["intent"] == "conversation"
    assert decision["needs_vector"] is False
    assert decision["needs_graph"] is False


def test_graph_and_hybrid_routing_policy_preserves_source_distinction():
    graph = apply_routing_policy("چه clash هایی بین لوله‌ها و تیرها وجود دارد؟", {
        "needs_vector": False, "needs_graph": True, "is_technical": True,
    })
    hybrid = apply_routing_policy("آیا فاصله این لوله از دیوار مطابق مقررات است؟", {
        "needs_vector": True, "needs_graph": True, "is_technical": True,
    })
    assert (graph["needs_vector"], graph["needs_graph"]) == (False, True)
    assert (hybrid["needs_vector"], hybrid["needs_graph"]) == (True, True)


def test_router_outage_fallback_still_distinguishes_explicit_graph_and_hybrid_queries():
    state = ConversationState("chat")
    graph = _safe_understanding_fallback(
        "چه clash هایی بین لوله‌ها و تیرها وجود دارد؟", state, RuntimeError("offline")
    )
    hybrid = _safe_understanding_fallback(
        "آیا فاصله این لوله از دیوار مطابق مقررات است؟", state, RuntimeError("offline")
    )
    assert (graph["needs_vector"], graph["needs_graph"]) == (False, True)
    assert (hybrid["needs_vector"], hybrid["needs_graph"]) == (True, True)


def test_follow_up_fallback_inherits_previous_source_route():
    state = ConversationState(
        "chat", topic="clash لوله و تیر", last_needs_vector=False, last_needs_graph=True
    )
    decision = _safe_understanding_fallback("بازم بگو", state, RuntimeError("offline"))
    assert (decision["needs_vector"], decision["needs_graph"]) == (False, True)


def test_semantic_multilingual_embeddings_group_persian_paraphrases():
    embedder = MultilingualEmbeddingFunction()
    texts = [
        "بازدید عینی از تاسیسات برقی",
        "بازرسی چشمی برق ساختمان",
        "در بازرسی ظاهری تاسیسات الکتریکی چه چیزهایی باید بررسی شود؟",
        "طرز تهیه کیک شکلاتی در آشپزخانه",
    ]
    vectors = np.asarray(embedder(texts))
    similarities = vectors[1:] @ vectors[0]
    assert similarities[0] > similarities[2]
    assert similarities[1] > similarities[2]


def test_hybrid_reranker_prefers_semantically_matching_engineering_text():
    candidates = [
        Candidate("electrical", "بازرسی ظاهری سیم ها، کابل ها و تابلوهای برق", {}, dense_score=0.72, query_hits=3),
        Candidate("unrelated", "ضوابط نگهداری آسانسور و پلکان برقی", {}, dense_score=0.30, query_hits=1),
    ]
    ranked = rerank_candidates("بازدید چشمی تاسیسات الکتریکی", candidates)
    assert ranked[0].chunk_id == "electrical"


def test_complete_section_expansion_makes_all_continuations_available(monkeypatch):
    seed = Candidate(
        "c1", "الف- مورد اول", {"document_id": "d", "section_id": "22-7-7", "page_number": 67,
        "chunk_index": 1, "clause_id": "22-7-7", "source": "22.pdf"}, rerank_score=0.9,
    )
    records = [
        {"id": "c1", "document": "الف- مورد اول", "metadata": seed.metadata},
        {"id": "c2", "document": "ب- مورد دوم", "metadata": {**seed.metadata, "page_number": 67, "chunk_index": 2}},
        {"id": "c3", "document": "پ- مورد سوم", "metadata": {**seed.metadata, "page_number": 68, "chunk_index": 3}},
    ]
    monkeypatch.setattr("rag.retrieval.get_section_chunks", lambda *args, **kwargs: records)
    expanded = _expand([seed], True, RAGSettings())
    context, sources, selected = assemble_context(expanded)
    assert {item.chunk_id for item in selected} == {"c1", "c2", "c3"}
    assert "مورد سوم" in context


def test_indexed_persian_paraphrases_deliver_complete_electrical_section():
    if collection_status()["count"] == 0:
        pytest.skip("Build the regulation index with python -m rag.indexer --rebuild")
    markers = ["الف-", "ب-", "پ-", "ت-", "ث -", "ج-", "چ-", "ح -", "خ -",
               "د -", "ذ-", "ر -", "ز -", "ژ-", "س-"]
    queries = [
        "بازدید عینی از تاسیسات برقی",
        "بازرسی چشمی برق ساختمان",
        "در بازرسی ظاهری تاسیسات الکتریکی چه چیزهایی باید بررسی شود؟",
    ]
    for query in queries:
        result = retrieve_regulations(query, completeness_requested=True)
        target = [item for item in result.chunks if item.metadata.get("clause_id") == "22-7-7"]
        assert len(target) == 3
        assert all(marker in result.context for marker in markers)


def test_conversation_memory_isolated_and_bounded():
    store = ConversationStore(RAGSettings(memory_recent_messages=2, memory_max_conversations=2))
    first = store.get("first")
    second = store.get("second")
    store.append_turn(first, "پرسش", "پاسخ", topic="برق")
    assert second.messages == []
    store.append_turn(first, "ادامه", "تکمیل", topic="برق")
    assert len(first.messages) == 2
    assert "پرسش" in first.summary
