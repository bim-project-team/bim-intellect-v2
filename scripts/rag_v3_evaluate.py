"""Audit-only runner for the frozen BIM-Intellect RAG v3 evaluation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
import uuid
from typing import Any

from rag.chunker import normalize_persian_text
from rag.config import SETTINGS
from rag.document_metadata import resolve_document_page_count
from rag.embedder import get_chunks_by_ids, list_indexed_documents
from rag.memory import ConversationStore
import rag.orchestrator as orchestrator_module
from rag.orchestrator import (
    CITATION_PATTERN,
    DOCUMENT_CITATION_PATTERN,
    RAGOrchestrator,
    extract_document_citations,
    extract_regulation_citations,
    validate_generated_answer,
)
from rag.retrieval import retrieve_regulations


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "rag_v3_evaluation_artifacts"
FROZEN_DIR = Path(os.getenv(
    "RAG_V3_EVAL_INDEX",
    str(Path(os.environ.get("TEMP", ".")) / "bim-intellect-rag-v3-eval-freeze-20260914"),
))
SETTINGS = replace(
    SETTINGS,
    chroma_dir=str(FROZEN_DIR),
    collection_name="regulations_improved_v3",
    index_version="3",
)


CASES: list[dict[str, Any]] = [
    {
        "id": "machine_room",
        "question": "در مورد ابعاد موتورخانه آسانسور راهنماییم کن",
        "variants": [],
        "sections": ["15-2-2-5", "15-2-2-5-1", "15-2-2-5-2", "15-2-2-5-3"],
        "citation_sections": ["15-2-2-5-2", "15-2-2-5-3"],
        "pages": [31],
        "complete": True,
        "facts": {
            "700 mm control-panel clearance": [["700", "میلی", "متر"]],
            "400 mm fixed-equipment passage": [["400", "میلی", "متر"]],
            "500 mm rotating-equipment passage": [["500", "متر"]],
            "2000 mm clear height": [["2000", "میلی", "متر"]],
            "300 mm rotating-part clearance": [["300", "میلی", "متر"]],
            "500 x 600 mm working area": [["500", "600", "متر"]],
            "shared machine room": [["مشترک", "موتورخانه"]],
        },
    },
    {
        "id": "stair_depth",
        "question": "What is the minimum landing depth for stairs according to Mabhas 15?",
        "variants": [],
        "sections": ["15-3-5-9"],
        "citation_sections": ["15-3-5-9"],
        "pages": [57],
        "complete": False,
        "facts": {
            "source notation 38/0 m": [["38/0", "متر"]],
            "normalized 0.38 m": [["0.38", "متر"]],
            "equivalent 38 cm": [["38", "سانتی", "متر"]],
        },
    },
    {
        "id": "vacuum_breaker",
        "question": "خلا شکن چیه؟",
        "variants": [],
        "sections": ["16-1"],
        "citation_sections": ["16-1"],
        "pages": [29],
        "complete": False,
        "facts": {
            "backflow prevention": [["جریان", "برگشت", "مانع"], ["backflow", "prevent"]],
            "atmospheric pressure": [["فشار", "اتمسفر"], ["atmospheric", "pressure"]],
            "air admission": [["هوا", "وارد"], ["air", "admit"]],
        },
    },
    {
        "id": "flange",
        "question": "استاندرادهایی که برای انتخاب فلنچ چدنی باید بدونیم چیا هستند",
        "variants": ["استانداردهای انتخاب فلنج چدنی", "فلنج چدنی EN1092 ISO7005 ASME"],
        "sections": ["16-3-4-5"],
        "citation_sections": ["16-3-4-5"],
        "pages": [69, 70, 72],
        "complete": True,
        "facts": {
            "EN1092-2": [["EN1092-2"]],
            "ISO7005-2": [["ISO7005-2"]],
            "ASME16.1": [["ASME16.1"]],
        },
    },
    {
        "id": "site_coverage",
        "question": "در مورد سطح اشغال و زیربنا در کدام مبحث مقررات ملی ساختمان صحبت شده و چه نکاتی گفته شده؟",
        "variants": ["الزامات سطح اشغال و زیربنا", "سطح اشغال زیربنا مبحث 24"],
        "sections": ["42-2-3-4", "42-2-3-4-1", "42-2-3-4-2", "42-2-3-4-3", "42-2-3-4-4"],
        "citation_sections": ["42-2-3-4-1", "42-2-3-4-2", "42-2-3-4-3", "42-2-3-4-4"],
        "pages": [38],
        "complete": True,
        "facts": {
            "minimum open space": [["حداقل", "فضای", "باز"]],
            "surrounding grain compatibility": [["دانه", "بافت", "پیرامون"]],
            "adjacent physical fabric": [["بافت", "کالبدی", "همجوار"]],
            "minimum permitted floor area": [["حداقل", "زیربنا", "مجاز"]],
        },
    },
    {
        "id": "page_count",
        "question": "مبحث ۲۴ چند صفحه است؟",
        "variants": [],
        "sections": [],
        "citation_sections": [],
        "pages": [],
        "complete": False,
        "metadata": True,
        "facts": {"55 physical PDF pages": [["55", "صفحه", "فیزیکی", "PDF"]]},
    },
    {
        "id": "electrical_complete",
        "question": "بازدید عینی از تأسیسات برقی شامل چه مواردی است؟",
        "variants": ["بازرسی چشمی برق ساختمان"],
        "sections": ["22-7-7"],
        "citation_sections": ["22-7-7"],
        "pages": [67, 68],
        "complete": True,
        "facts": {},
        "expected_list_items": 15,
    },
    {
        "id": "call_system",
        "question": "سيستمهاي فراخواني آسانسور به رو توضیح بده",
        "variants": [],
        "sections": ["15-1-2", "15-2-5-6"],
        "citation_sections": ["15-1-2", "15-2-5-6"],
        "pages": [],
        "complete": False,
        "facts": {},
    },
    {
        "id": "site_exact_heading",
        "question": "الزامات سطح اشغال و زیربنا در مبحث ۲۴ چیست؟",
        "variants": [],
        "sections": ["42-2-3-4", "42-2-3-4-1", "42-2-3-4-2", "42-2-3-4-3", "42-2-3-4-4"],
        "citation_sections": ["42-2-3-4-1", "42-2-3-4-2", "42-2-3-4-3", "42-2-3-4-4"],
        "pages": [38],
        "complete": True,
        "facts": {
            "minimum open space": [["حداقل", "فضای", "باز"]],
            "surrounding grain compatibility": [["دانه", "بافت", "پیرامون"]],
            "adjacent physical fabric": [["بافت", "کالبدی", "همجوار"]],
            "minimum permitted floor area": [["حداقل", "زیربنا", "مجاز"]],
        },
    },
]


NEGATIVE_CASES = [
    {
        "id": "liquid_hydrogen_pressure",
        "question": "طبق مبحث ۱۶، حداکثر فشار مجاز لوله کشی هیدروژن مایع در ساختمان چند پاسکال است؟",
    },
    {
        "id": "space_elevator_standard",
        "question": "شماره استاندارد الزامی آسانسور فضایی در مبحث ۱۵ چیست؟",
    },
    {
        "id": "mars_helipad_coverage",
        "question": "مبحث ۲۴ چه درصد سطح اشغالی برای باند فرود هلیکوپتر روی مریخ تعیین کرده است؟",
    },
]


COMPLETENESS_QUESTIONS = [
    "همه استانداردهای فلنج چدنی را فهرست کن.",
    "تمام گزینه های استاندارد برای انتخاب فلنج چدنی را بده.",
    "هر استاندارد مربوط به فلنج چدنی را بدون حذف موردی استخراج کن.",
    "What are all standards for cast-iron flanges?",
    "جدول کامل استانداردهای انتخاب فلنج چدنی را استخراج کن.",
]


FOLLOW_UPS = {
    "stair": [
        "What is the minimum landing depth for stairs according to Mabhas 15?",
        "عمق پله؟",
        "۳۸ متر؟؟؟",
    ],
    "flange": [
        "What standards apply to cast-iron flanges?",
        "Which ones are ISO standards?",
        "List all of them again.",
    ],
}


def _norm(value: str) -> str:
    value = normalize_persian_text(value).casefold()
    return re.sub(r"[\s‌ـ_.,،؛:;()\[\]`*'\"-]+", "", value)


def _fact_hits(text: str, case: dict[str, Any]) -> dict[str, bool]:
    normalized = _norm(text)
    output = {}
    for label, alternatives in case.get("facts", {}).items():
        output[label] = any(all(_norm(term) in normalized for term in terms) for terms in alternatives)
    return output


def _section_matches(value: Any, expected: str) -> bool:
    actual = str(value or "")
    return actual == expected or actual.startswith(expected + "-") or expected.startswith(actual + "-")


def _first_rank(rows: list[dict[str, Any]], expected: list[str]) -> int | None:
    for index, row in enumerate(rows, 1):
        if any(_section_matches(row.get("section_id"), section) for section in expected):
            return index
    return None


def _write(name: str, payload: Any) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / name
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {target} ({target.stat().st_size} bytes)", flush=True)


def _base_payload(phase: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "frozen_index": str(FROZEN_DIR),
        "collection": SETTINGS.collection_name,
        "index_version": SETTINGS.index_version,
        "configuration": asdict(SETTINGS),
    }


def run_retrieval() -> None:
    payload = _base_payload("fixed_retrieval")
    results = []
    metric_cases = []
    for case in CASES:
        if case.get("metadata"):
            answer = resolve_document_page_count(
                case["question"], list_indexed_documents(settings=SETTINGS),
            )
            results.append({
                "id": case["id"], "question": case["question"], "metadata_answer": asdict(answer) if answer else None,
                "fact_hits": _fact_hits(answer.answer if answer else "", case),
            })
            continue
        started = time.perf_counter()
        retrieval = retrieve_regulations(
            case["question"], case["variants"],
            completeness_requested=case["complete"], settings=SETTINGS,
        )
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        diagnostics = asdict(retrieval.diagnostics)
        selected_sections = [str(item.metadata.get("section_id", "")) for item in retrieval.chunks]
        selected_pages = [int(item.metadata.get("pdf_page_number") or item.metadata.get("page_number") or 0) for item in retrieval.chunks]
        exact_section_hits = {section: section in set(selected_sections) for section in case["sections"]}
        page_hits = {str(page): page in set(selected_pages) for page in case["pages"]}
        fact_hits = _fact_hits(retrieval.context, case)
        table_rows = None
        if case["id"] == "flange":
            table_rows = {code: _norm(code) in _norm(retrieval.context) for code in ("EN1092-2", "ISO7005-2", "ASME16.1")}
        result = {
            "id": case["id"],
            "original_question": case["question"],
            "standalone_query": case["question"],
            "fixed_variants": case["variants"],
            "completeness_requested": case["complete"],
            "latency_ms": elapsed,
            "expected_sections": case["sections"],
            "expected_pages": case["pages"],
            "dense_rank": _first_rank(diagnostics["dense_top"], case["sections"]),
            "lexical_rank": _first_rank(diagnostics["lexical_top"], case["sections"]),
            "rerank_rank": _first_rank(diagnostics["reranked_top"], case["sections"]),
            "exact_section_hits": exact_section_hits,
            "page_hits": page_hits,
            "fact_hits": fact_hits,
            "table_row_hits": table_rows,
            "diagnostics": diagnostics,
            "sources": retrieval.sources,
            "final_context": retrieval.context,
        }
        results.append(result)
        metric_cases.append(result)
        print(f"RETRIEVAL {case['id']} rank={result['rerank_rank']} sections={sum(exact_section_hits.values())}/{len(exact_section_hits)}", flush=True)

    ks = (1, 3, 5, 10)
    ranks = [item["rerank_rank"] for item in metric_cases]
    section_total = sum(len(item["exact_section_hits"]) for item in metric_cases)
    section_hits = sum(sum(item["exact_section_hits"].values()) for item in metric_cases)
    page_total = sum(len(item["page_hits"]) for item in metric_cases)
    page_hits = sum(sum(item["page_hits"].values()) for item in metric_cases)
    fact_total = sum(len(item["fact_hits"]) for item in metric_cases)
    fact_hits = sum(sum(item["fact_hits"].values()) for item in metric_cases)
    payload["metrics"] = {
        **{f"recall_at_{k}": sum(rank is not None and rank <= k for rank in ranks) / len(ranks) for k in ks},
        "mrr": sum(1 / rank if rank else 0 for rank in ranks) / len(ranks),
        "correct_section_rate_at_10": sum(rank is not None for rank in ranks) / len(ranks),
        "complete_section_coverage": {"hits": section_hits, "total": section_total, "rate": section_hits / section_total},
        "correct_page_coverage": {"hits": page_hits, "total": page_total, "rate": page_hits / page_total},
        "final_context_fact_coverage": {"hits": fact_hits, "total": fact_total, "rate": fact_hits / fact_total if fact_total else None},
        "flange_table_row_coverage": results[3]["table_row_hits"],
    }
    payload["results"] = results
    _write("retrieval_raw.json", payload)
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


def _provider_recorder(events: list[dict[str, Any]]):
    original = orchestrator_module.call_with_retries

    def recorded(fn, *, op_name: str, max_retries: int | None = None):
        started = time.perf_counter()
        try:
            if max_retries is None:
                result = original(fn, op_name=op_name)
            else:
                result = original(fn, op_name=op_name, max_retries=max_retries)
            events.append({"operation": op_name, "success": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)})
            return result
        except Exception as exc:
            events.append({"operation": op_name, "success": False, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": f"{type(exc).__name__}: {exc}"})
            raise

    return original, recorded


def _unsupported_numbers(answer: str, evidence: str) -> list[str]:
    answer = CITATION_PATTERN.sub("", answer)
    answer = DOCUMENT_CITATION_PATTERN.sub("", answer)
    answer = re.sub(r"(?m)^\s*\d+\s*[.)-]\s*", "", answer)
    translate = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    number_pattern = re.compile(r"(?<![\w-])\d+(?:[./]\d+)?")
    claimed = set(number_pattern.findall(answer.translate(translate)))
    supported = set(number_pattern.findall(evidence.translate(translate)))
    return sorted(claimed - supported)


def _insufficient(answer: str) -> bool:
    return bool(re.search(
        r"(?:اطلاعات|شواهد|منبع|مدرک).{0,45}(?:کافی|موجود|یافت|ندارد|نیست)|"
        r"(?:پیدا|یافت).{0,30}نشد|insufficient|not enough|no (?:relevant )?(?:evidence|information)|not (?:specified|found)",
        answer, re.IGNORECASE,
    ))


def _answer_case(orchestrator: RAGOrchestrator, case: dict[str, Any], strong: bool, conversation_id: str | None = None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    original, recorded = _provider_recorder(events)
    orchestrator_module.call_with_retries = recorded
    started = time.perf_counter()
    error = None
    try:
        response = orchestrator.ask(
            case["question"], conversation_id=conversation_id or str(uuid.uuid4()),
            use_strong_models=strong,
        )
    except Exception as exc:
        response = None
        error = f"{type(exc).__name__}: {exc}"
    finally:
        orchestrator_module.call_with_retries = original
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    if response is None:
        return {"id": case.get("id"), "question": case["question"], "error": error, "latency_ms": elapsed, "provider_events": events}

    selected_debug = response.get("retrieval_debug", {}).get("selected_chunks", [])
    ids = [str(item.get("chunk_id")) for item in selected_debug if item.get("chunk_id")]
    records = get_chunks_by_ids(ids, settings=SETTINGS)
    evidence = "\n".join(record["document"] for record in records)
    answer = str(response.get("answer", ""))
    fact_hits = _fact_hits(answer, case)
    completeness = response.get("retrieval_debug", {}).get("completeness", {})
    if case.get("expected_list_items"):
        fact_hits = {
            f"enumerated item {index + 1}": index < int(completeness.get("covered_items") and len(completeness["covered_items"]) or 0)
            for index in range(case["expected_list_items"])
        }
    expected_citations = case.get("citation_sections", [])
    cited_source_sections = {
        str(source.get("section_id", "")) for source in response.get("sources", [])
        if source.get("type", "regulation") == "regulation"
    }
    citation_section_hits = {
        section: any(_section_matches(actual, section) for actual in cited_source_sections)
        for section in expected_citations
    }
    citation_valid = (
        True if response.get("sources") and response["sources"][0].get("type") == "document_metadata"
        else validate_generated_answer(answer, response.get("sources", []), bool(response.get("used_vector")))
    )
    return {
        "id": case.get("id"),
        "question": case["question"],
        "profile": "strong" if strong else "standard",
        "latency_ms": elapsed,
        "router_model": response.get("router_model"),
        "final_model": response.get("final_model"),
        "rewritten_query": response.get("rewritten_query"),
        "retrieval_queries": response.get("retrieval_debug", {}).get("retrieval_queries", []),
        "retrieved_context_ids": ids,
        "retrieval_debug": response.get("retrieval_debug", {}),
        "answer": answer,
        "sources": response.get("sources", []),
        "recognized_citations": [asdict(item) for item in extract_regulation_citations(answer)],
        "recognized_document_citations": [asdict(item) for item in extract_document_citations(answer)],
        "fact_hits": fact_hits,
        "required_fact_coverage": {
            "hits": sum(fact_hits.values()), "total": len(fact_hits),
            "rate": sum(fact_hits.values()) / len(fact_hits) if fact_hits else None,
        },
        "citation_valid": citation_valid,
        "citation_section_hits": citation_section_hits,
        "citation_completeness": (
            sum(citation_section_hits.values()) / len(citation_section_hits)
            if citation_section_hits else None
        ),
        "unsupported_numeric_claims": _unsupported_numbers(answer, evidence),
        "insufficient_evidence_response": _insufficient(answer),
        "provider_events": events,
        "error": error,
    }


def _profile(strong: bool) -> str:
    return "strong" if strong else "standard"


def run_e2e(strong: bool) -> None:
    profile = _profile(strong)
    payload = _base_payload(f"e2e_{profile}")
    orchestrator = RAGOrchestrator(settings=SETTINGS, memory=ConversationStore(SETTINGS))
    results = []
    for case in CASES:
        result = _answer_case(orchestrator, case, strong)
        results.append(result)
        print(f"E2E {profile} {case['id']} facts={result.get('required_fact_coverage')} citations={result.get('citation_valid')}", flush=True)
    payload["results"] = results
    payload["metrics"] = _aggregate_answers(results)
    _write(f"e2e_{profile}_raw.json", payload)
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


def _aggregate_answers(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if not item.get("error")]
    fact_hits = sum(item.get("required_fact_coverage", {}).get("hits", 0) for item in completed)
    fact_total = sum(item.get("required_fact_coverage", {}).get("total", 0) for item in completed)
    citation_cases = [item for item in completed if item.get("citation_valid") is not None]
    citation_completeness = [item["citation_completeness"] for item in completed if item.get("citation_completeness") is not None]
    return {
        "completed_cases": len(completed),
        "total_cases": len(results),
        "required_fact_coverage": {"hits": fact_hits, "total": fact_total, "rate": fact_hits / fact_total if fact_total else None},
        "citation_correctness": sum(bool(item["citation_valid"]) for item in citation_cases) / len(citation_cases) if citation_cases else None,
        "mean_citation_completeness": sum(citation_completeness) / len(citation_completeness) if citation_completeness else None,
        "cases_with_unsupported_numeric_claims": sum(bool(item.get("unsupported_numeric_claims")) for item in completed),
        "provider_operation_failures": sum(not event["success"] for item in results for event in item.get("provider_events", [])),
    }


def run_negative(strong: bool) -> None:
    profile = _profile(strong)
    payload = _base_payload(f"negative_{profile}")
    orchestrator = RAGOrchestrator(settings=SETTINGS, memory=ConversationStore(SETTINGS))
    results = []
    for raw in NEGATIVE_CASES:
        case = {**raw, "facts": {}, "citation_sections": []}
        result = _answer_case(orchestrator, case, strong)
        result["correct_insufficient_behavior"] = bool(
            not result.get("error")
            and result.get("insufficient_evidence_response")
            and not result.get("unsupported_numeric_claims")
            and result.get("citation_valid")
        )
        results.append(result)
        print(f"NEGATIVE {profile} {case['id']} correct={result['correct_insufficient_behavior']}", flush=True)
    payload["results"] = results
    payload["metrics"] = {
        "correct_insufficient_rate": sum(item["correct_insufficient_behavior"] for item in results) / len(results),
        "hallucination_rate": sum(not item["correct_insufficient_behavior"] for item in results) / len(results),
        "invented_or_invalid_citation_rate": sum(item.get("citation_valid") is False for item in results) / len(results),
        "unsupported_numeric_rate": sum(bool(item.get("unsupported_numeric_claims")) for item in results) / len(results),
    }
    _write(f"negative_{profile}_raw.json", payload)
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


def run_completeness(strong: bool) -> None:
    profile = _profile(strong)
    payload = _base_payload(f"completeness_{profile}")
    orchestrator = RAGOrchestrator(settings=SETTINGS, memory=ConversationStore(SETTINGS))
    base = next(case for case in CASES if case["id"] == "flange")
    results = []
    for index, question in enumerate(COMPLETENESS_QUESTIONS, 1):
        case = {**base, "id": f"flange_complete_{index}", "question": question, "variants": []}
        result = _answer_case(orchestrator, case, strong)
        result["all_rows"] = all(result.get("fact_hits", {}).values())
        result["invented_standard_like_tokens"] = sorted({
            token for token in re.findall(r"\b[A-Z]{2,}(?:\s?B)?\s*\d+(?:[.-]\d+)*\b", result.get("answer", ""))
            if _norm(token) not in _norm("EN1092-2 ISO7005-2 ASME16.1")
        })
        results.append(result)
        print(f"COMPLETE {profile} {index} rows={result['all_rows']} invented={result['invented_standard_like_tokens']}", flush=True)
    payload["results"] = results
    payload["metrics"] = {
        "all_row_rate": sum(item["all_rows"] for item in results) / len(results),
        "no_invented_row_rate": sum(not item["invented_standard_like_tokens"] for item in results) / len(results),
        "citation_correctness": sum(bool(item.get("citation_valid")) for item in results) / len(results),
        "mean_citation_completeness": sum(float(item.get("citation_completeness") or 0) for item in results) / len(results),
    }
    _write(f"completeness_{profile}_raw.json", payload)
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


def run_followups(strong: bool) -> None:
    profile = _profile(strong)
    payload = _base_payload(f"followups_{profile}")
    orchestrator = RAGOrchestrator(settings=SETTINGS, memory=ConversationStore(SETTINGS))
    conversations = []
    for name, turns in FOLLOW_UPS.items():
        conversation_id = f"eval-{profile}-{name}-{uuid.uuid4()}"
        turn_results = []
        for index, question in enumerate(turns, 1):
            if name == "stair":
                base = next(case for case in CASES if case["id"] == "stair_depth")
            else:
                base = next(case for case in CASES if case["id"] == "flange")
            case = {**base, "id": f"{name}_turn_{index}", "question": question, "variants": []}
            result = _answer_case(orchestrator, case, strong, conversation_id)
            expected_section = base["sections"][0]
            selected_sections = {
                str(item.get("section_id", ""))
                for item in result.get("retrieval_debug", {}).get("selected_chunks", [])
            }
            result["subject_section_retained"] = any(_section_matches(value, expected_section) for value in selected_sections)
            turn_results.append(result)
            print(f"FOLLOWUP {profile} {name} turn={index} retained={result['subject_section_retained']}", flush=True)
        conversations.append({"id": name, "conversation_id": conversation_id, "turns": turn_results})
    all_followups = [turn for conv in conversations for turn in conv["turns"][1:]]
    payload["conversations"] = conversations
    payload["metrics"] = {
        "subject_retention_rate": sum(item["subject_section_retained"] for item in all_followups) / len(all_followups),
        "citation_correctness": sum(bool(item.get("citation_valid")) for item in all_followups) / len(all_followups),
        "stair_final_has_normalized_value": bool(conversations[0]["turns"][-1].get("fact_hits", {}).get("normalized 0.38 m")),
        "flange_final_all_rows": all(conversations[1]["turns"][-1].get("fact_hits", {}).values()),
    }
    _write(f"followups_{profile}_raw.json", payload)
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("retrieval", "e2e", "negative", "completeness", "followups"))
    parser.add_argument("--profile", choices=("standard", "strong"))
    args = parser.parse_args()
    if not FROZEN_DIR.exists():
        raise SystemExit(f"Frozen index is missing: {FROZEN_DIR}")
    if args.phase != "retrieval" and not args.profile:
        parser.error("--profile is required for model-backed phases")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    logging.getLogger().addHandler(
        logging.FileHandler(ARTIFACTS / "evaluation.log", encoding="utf-8")
    )
    if args.phase == "retrieval":
        run_retrieval()
        return
    strong = args.profile == "strong"
    {"e2e": run_e2e, "negative": run_negative, "completeness": run_completeness, "followups": run_followups}[args.phase](strong)


if __name__ == "__main__":
    main()
