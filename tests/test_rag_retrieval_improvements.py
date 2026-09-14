import json
from pathlib import Path

import pytest

from rag.chunker import (
    ExtractedTable,
    _table_text_parts,
    chunk_pdf_with_diagnostics,
    find_clause_matches,
    persian_decimal_aliases,
)
from rag.config import RAGSettings, get_model_profile
from rag.document_metadata import resolve_document_page_count
from rag.indexer import _KNOWN_CLAUSE_ROOT_CORRECTIONS
from rag.memory import ConversationState
from rag.openrouter_client import LLMConfigError
from rag.orchestrator import (
    RAGOrchestrator, RetrievalResult,
    is_completeness_sensitive,
    validate_answer_completeness,
)
from rag.reranker import Candidate, normalize_retrieval_query, rerank_candidates
from rag.retrieval import _expand, assemble_context


MABHAS_16 = Path("dataset/sources/16 - v4-1396 - تاسیسات بهداشتی.pdf")


def test_standard_identifiers_are_not_misclassified_as_clauses():
    text = "16-3-4-5 انتخاب فلنج\nASME16.1\nASME B 16.5\n16-3-4-6 انتخاب شیر"
    assert [item.clause_id for item in find_clause_matches(text, "16")] == [
        "16-3-4-5", "16-3-4-6",
    ]


def test_table_grid_serialization_preserves_header_value_relationships():
    table = ExtractedTable(70, 0, [
        ["فلنج فولادی", "فلنج چدنی"],
        ["EN1092-1", "EN1092-2"],
        ["ISO7005-1", "ISO7005-2"],
        ["ASME16.5", "ASME16.1"],
    ])
    rendered = "\n".join(_table_text_parts(table, 1200))
    assert "فلنج چدنی=EN1092-2" in rendered
    assert "فلنج چدنی=ISO7005-2" in rendered
    assert "فلنج چدنی=ASME16.1" in rendered


@pytest.fixture(scope="module")
def mabhas_16_chunks():
    if not MABHAS_16.exists():
        pytest.skip("Mabhas 16 regression PDF is not available")
    return chunk_pdf_with_diagnostics(MABHAS_16, doc_id="m16", source=MABHAS_16.name)


def test_flange_table_is_one_citable_section_with_page_concepts(mabhas_16_chunks):
    table_chunks = [
        chunk for chunk in mabhas_16_chunks.chunks
        if chunk.page_number == 70 and chunk.chunk_kind == "table" and "ASME16.1" in chunk.text
    ]
    assert len(table_chunks) == 1
    chunk = table_chunks[0]
    assert chunk.clause_id == "16-3-4-5"
    assert chunk.printed_page_number == 54
    assert chunk.pdf_page_number == 70
    assert chunk.total_pdf_pages == 236
    assert all(value in chunk.text for value in ("EN1092-2", "ISO7005-2", "ASME16.1"))


@pytest.mark.parametrize("source,normalized", [
    ("حداقل عمق 38/0 متر", "0.38"),
    ("حداقل عمق ۳۸/۰ متر", "0.38"),
    ("سرعت ٥ / ٢ متر بر ثانیه", "2.5"),
])
def test_persian_slash_decimals_are_normalized_only_with_measurement_context(source, normalized):
    aliases = persian_decimal_aliases(source)
    assert aliases and aliases[0]["normalized"] == normalized
    assert persian_decimal_aliases("مصوبه مورخ 16/14/1436") == []


def test_context_preserves_original_number_and_adds_deterministic_alias():
    candidate = Candidate("c", "حداقل عمق 38/0 متر است", {
        "document_id": "d", "source": "15.pdf", "section_id": "15-3-5-9",
        "clause_id": "15-3-5-9", "page_number": 57, "chunk_index": 1,
    }, rerank_score=1.0)
    context, _, _ = assemble_context([candidate])
    assert "38/0 متر" in context
    assert "38/0 متر = 0.38 متر" in context


def test_extractive_fallback_preserves_and_interprets_source_decimal():
    candidate = Candidate("c", "حداقل عمق 38/0 متر است", {
        "document_id": "d", "source": "15.pdf", "section_id": "15-3-5-9",
        "clause_id": "15-3-5-9", "page_number": 57,
    }, rerank_score=1.0)
    regulation = type("Regulation", (), {"chunks": [candidate]})()
    answer = RAGOrchestrator._extractive_answer(RetrievalResult(regulation=regulation))
    assert "38/0 متر" in answer
    assert "0.38 متر" in answer
    assert "[Clause 15-3-5-9, Page 57]" in answer


def test_completeness_detection_is_deterministic_for_client_requests():
    assert is_completeness_sensitive("استاندرادهایی که برای انتخاب فلنچ چدنی باید بدونیم چیا هستند")
    assert is_completeness_sensitive("در مورد ابعاد موتورخانه آسانسور راهنماییم کن")
    assert not is_completeness_sensitive("خلا شکن چیه؟")
    assert normalize_retrieval_query("استاندراد فلنچ") == "استاندارد فلنج"
    assert normalize_retrieval_query("بازرسی ظاهری تاسیسات الکتریکی") == "بازدید عینی تاسیسات برقی"


def test_table_completeness_validator_detects_missing_supported_standard():
    rows = [["فلنج فولادی", "فلنج چدنی"], ["EN1092-1", "EN1092-2"],
            ["ISO7005-1", "ISO7005-2"], ["ASME16.5", "ASME16.1"]]
    candidate = Candidate("table", "structured table", {
        "table_data_json": json.dumps(rows, ensure_ascii=False), "chunk_kind": "table",
    })
    incomplete = validate_answer_completeness(
        "EN1092-2 و ISO7005-2", "استانداردهای فلنج چدنی", [candidate], True,
    )
    assert not incomplete.valid
    assert incomplete.missing_items == ["ASME16.1"]
    complete = validate_answer_completeness(
        "EN1092-2، ISO7005-2 و ASME 16.1", "استانداردهای فلنج چدنی", [candidate], True,
    )
    assert complete.valid


def test_completeness_uses_one_coherent_enumeration_and_finds_inline_first_item():
    target = Candidate("target", "مقدمه الف- تابلوها\nب- سیم ها\nپ- مدارک\nت- عایق ها", {
        "document_id": "m22", "section_id": "22-7-7",
    })
    neighbour = Candidate("other", "الف- نامرتبط\nب- نامرتبط دوم\nپ- نامرتبط سوم\nت- نامرتبط چهارم", {
        "document_id": "m22", "section_id": "22-7-9",
    })
    result = validate_answer_completeness(
        "تابلوها، سیم ها، مدارک و عایق ها", "چه مواردی در بازدید عینی است؟",
        [target, neighbour], True,
    )
    assert result.valid
    assert result.expected_items[0].startswith("الف-")
    assert all("نامرتبط" not in item for item in result.expected_items)


def test_heading_and_document_number_boost_exact_section():
    candidates = [
        Candidate("correct", "الزامات سطح اشغال و زیربنا", {
            "heading": "الزامات سطح اشغال و زیربنا", "document_number": "24",
        }, dense_score=0.55),
        Candidate("wrong", "سطح اشغال و تراکم در ساختمان", {
            "heading": "قواعد عمومی تراکم", "document_number": "12",
        }, dense_score=0.80),
    ]
    ranked = rerank_candidates(
        "الزامات سطح اشغال و زیربنا در مبحث 24", candidates, settings=RAGSettings(),
    )
    assert ranked[0].chunk_id == "correct"
    assert ranked[0].heading_score == 1.0
    assert ranked[0].document_score == 1.0


def test_completeness_expands_heading_parent_and_child_sections(monkeypatch):
    seed = Candidate("parent", "الزامات سطح اشغال و زیربنا", {
        "document_id": "m24", "section_id": "42-2-3-4", "page_number": 38,
        "chunk_index": 1, "clause_id": "42-2-3-4", "source": "24.pdf",
    }, rerank_score=0.9, heading_score=1.0)
    records = [
        {"id": "parent", "document": seed.text, "metadata": seed.metadata},
        {"id": "child", "document": "حداقل فضای باز", "metadata": {
            **seed.metadata, "section_id": "42-2-3-4-1", "clause_id": "42-2-3-4-1",
            "chunk_index": 2,
        }},
    ]
    monkeypatch.setattr("rag.retrieval.get_section_tree_chunks", lambda *args, **kwargs: records)
    expanded = _expand([seed], True, RAGSettings())
    assert {item.chunk_id for item in expanded} == {"parent", "child"}
    assert next(item for item in expanded if item.chunk_id == "child").expansion_reason == "complete_section_tree"


def test_lower_ranked_table_part_makes_its_text_section_structural(monkeypatch):
    metadata = {
        "document_id": "m16", "section_id": "16-3-4-5", "page_number": 70,
        "clause_id": "16-3-4-5", "source": "16.pdf",
    }
    text_seed = Candidate("text", "فلنج چدنی", metadata, rerank_score=0.9)
    table_part = Candidate(
        "table", "ASME16.1", {**metadata, "chunk_kind": "table"}, rerank_score=0.7,
    )
    calls = []
    monkeypatch.setattr(
        "rag.retrieval.get_section_tree_chunks",
        lambda _document, section, **_kwargs: calls.append(section) or [],
    )
    monkeypatch.setattr("rag.retrieval.get_section_chunks", lambda *args, **kwargs: [])
    _expand([text_seed, table_part], True, RAGSettings())
    assert calls == ["16-3-4-5"]


def test_table_discovered_during_section_expansion_blocks_broad_parent_inference(monkeypatch):
    common = {
        "document_id": "m16", "source": "16.pdf", "page_number": 70,
        "parent_section_id": "16-3-4",
    }
    ranked = [
        Candidate("flange", "فلنج", {
            **common, "section_id": "16-3-4-5", "clause_id": "16-3-4-5",
        }, rerank_score=0.9),
        Candidate("valve", "شیر", {
            **common, "section_id": "16-3-4-4", "clause_id": "16-3-4-4",
        }, rerank_score=0.7),
    ]
    tree_calls = []
    monkeypatch.setattr(
        "rag.retrieval.get_section_chunks",
        lambda _document, section, **_kwargs: [{
            "id": f"{section}-table", "document": "جدول", "metadata": {
                **common, "section_id": section, "clause_id": section,
                "chunk_kind": "table" if section == "16-3-4-5" else "text",
            },
        }],
    )
    monkeypatch.setattr(
        "rag.retrieval.get_section_tree_chunks",
        lambda _document, section, **_kwargs: tree_calls.append(section) or [],
    )
    _expand(ranked, True, RAGSettings())
    assert "16-3-4" not in tree_calls


def test_completeness_infers_shared_parent_from_multiple_ranked_leaves(monkeypatch):
    common = {
        "document_id": "m15", "source": "15.pdf", "page_number": 31,
        "parent_section_id": "15-2-2-5",
    }
    ranked = [
        Candidate("leaf-1", "جانمایی", {
            **common, "section_id": "15-2-2-5-1", "clause_id": "15-2-2-5-1",
        }, rerank_score=0.8),
        Candidate("leaf-3", "موتورخانه مشترک", {
            **common, "section_id": "15-2-2-5-3", "clause_id": "15-2-2-5-3",
        }, rerank_score=0.75),
    ]
    missing = {"id": "leaf-2", "document": "همه ابعاد", "metadata": {
        **common, "section_id": "15-2-2-5-2", "clause_id": "15-2-2-5-2",
    }}
    monkeypatch.setattr(
        "rag.retrieval.get_section_tree_chunks",
        lambda _document, section, **_kwargs: [missing] if section == "15-2-2-5" else [],
    )
    monkeypatch.setattr("rag.retrieval.get_section_chunks", lambda *args, **kwargs: [])
    expanded = _expand(ranked, True, RAGSettings())
    inferred = next(item for item in expanded if item.chunk_id == "leaf-2")
    assert inferred.expansion_reason == "inferred_complete_parent"


def test_specific_ranked_descendant_prevents_broad_parent_tree_expansion(monkeypatch):
    parent = Candidate("parent", "بازرسی تأسیسات برقی", {
        "document_id": "m22", "source": "22.pdf", "section_id": "22-7",
        "clause_id": "22-7", "page_number": 65,
    }, rerank_score=0.8, heading_score=1.0)
    child = Candidate("child", "بازدید عینی", {
        "document_id": "m22", "source": "22.pdf", "section_id": "22-7-7",
        "parent_section_id": "22-7", "clause_id": "22-7-7", "page_number": 67,
    }, rerank_score=0.9)
    tree_calls = []
    monkeypatch.setattr(
        "rag.retrieval.get_section_tree_chunks",
        lambda _document, section, **_kwargs: tree_calls.append(section) or [],
    )
    monkeypatch.setattr("rag.retrieval.get_section_chunks", lambda *args, **kwargs: [])
    sibling = Candidate("sibling", "آزمون عایقی", {
        "document_id": "m22", "source": "22.pdf", "section_id": "22-7-6",
        "parent_section_id": "22-7", "clause_id": "22-7-6", "page_number": 66,
    }, rerank_score=0.7)
    _expand([child, parent, sibling], True, RAGSettings())
    assert "22-7" not in tree_calls


def test_table_and_text_chunks_keep_one_backward_compatible_citation():
    metadata = {
        "document_id": "m16", "source": "16.pdf", "section_id": "16-3-4-5",
        "clause_id": "16-3-4-5", "page_number": 70, "pdf_page_number": 70,
        "printed_page_number": 54, "total_pdf_pages": 236,
    }
    candidates = [
        Candidate("text", "متن اصلی جدول", {**metadata, "chunk_kind": "text"}, rerank_score=1.0),
        Candidate("table", "ASME16.1", {**metadata, "chunk_kind": "table"}, rerank_score=0.9),
    ]
    _, sources, selected = assemble_context(candidates)
    assert len(selected) == 2
    assert len(sources) == 1
    assert sources[0]["clause_id"] == "16-3-4-5"
    from rag.orchestrator import validate_generated_answer
    assert validate_generated_answer("[Clause 16-3-4-5, Page 70]", sources, True)


def test_neighbor_expansion_starts_only_from_top_three(monkeypatch):
    ranked = [
        Candidate(str(index), str(index), {
            "document_id": "d", "source": "d.pdf", "section_id": str(index),
            "clause_id": f"1-{index}", "page_number": index,
            "next_section_chunk_id": f"n{index}",
        }, rerank_score=1.0 - index / 100)
        for index in range(10)
    ]
    requested_ids = []
    monkeypatch.setattr(
        "rag.retrieval.get_chunks_by_ids",
        lambda ids, **_kwargs: requested_ids.extend(ids) or [],
    )
    _expand(ranked, False, RAGSettings(neighbor_window=1))
    assert {value for value in requested_ids if value} == {"n0", "n1", "n2"}


def test_mabhas_24_internal_clause_root_is_not_rewritten_to_document_number():
    assert ("24", "42") not in _KNOWN_CLAUSE_ROOT_CORRECTIONS
    assert _KNOWN_CLAUSE_ROOT_CORRECTIONS[("12", "01")] == "12"


def test_page_count_is_resolved_from_deterministic_pdf_metadata():
    result = resolve_document_page_count("مبحث ۲۴ چند صفحه است؟", [{
        "document_id": "m24", "filename": "24 - v1.pdf", "document_number": "24",
        "total_pdf_pages": 55, "page_count": 48,
    }])
    assert result is not None
    assert "55 صفحه فیزیکی PDF" in result.answer
    assert result.source["total_pdf_pages"] == 55


def test_numeric_disbelief_follow_up_gets_explicit_correction_query(monkeypatch):
    state = ConversationState(
        "chat", topic="حداقل عمق پله مبحث 15",
        last_needs_vector=True, last_needs_graph=False,
    )
    orchestrator = RAGOrchestrator.__new__(RAGOrchestrator)
    orchestrator.settings = RAGSettings()
    def fail(*args, **kwargs):
        raise LLMConfigError("offline")
    monkeypatch.setattr("rag.orchestrator.call_with_retries", fail)
    profile = get_model_profile(False, orchestrator.settings)
    decision = orchestrator.understand("۳۸ متر؟؟؟", state, profile)
    assert decision["is_follow_up"] is True
    assert "رفع ابهام مقدار و واحد" in decision["standalone_query"]
    assert "حداقل عمق پله" in decision["standalone_query"]
