import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rag.orchestrator import RAGOrchestrator, apply_routing_policy


def _route_with_llm_decision(question, decision):
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(decision, ensure_ascii=False))
        )]
    )
    orchestrator = RAGOrchestrator.__new__(RAGOrchestrator)
    with patch("rag.orchestrator.call_with_retries", return_value=response):
        return orchestrator.route(question)


@pytest.mark.parametrize(
    ("question", "llm_decision", "expected"),
    [
        (
            "نحوه استفاده از سیلندر گاز تحت فشار رو توضیح بده",
            # Reproduce the original bad source decision while retaining the
            # router's semantic classification that this is technical.
            (False, False, True),
            (True, False),
        ),
        (
            "نگهداری و استفاده از سیلندر گاز تحت فشار",
            (True, False, True),
            (True, False),
        ),
        (
            "فاصله ایمن سیلندر گاز از منبع حرارتی چقدر باید باشد؟",
            (True, False, True),
            (True, False),
        ),
        (
            "سیلندر گاز رو چطور باید استفاده کرد؟",
            (True, False, True),
            (True, False),
        ),
        (
            "شرایط استفاده از کپسول گاز چیه؟",
            (True, False, True),
            (True, False),
        ),
        (
            "چه clash هایی بین لوله ها و تیرها وجود دارد؟",
            (False, True, True),
            (False, True),
        ),
        (
            "فاصله لوله شماره ۱۲ از دیوار چقدر است؟",
            (False, True, True),
            (False, True),
        ),
        (
            "آیا فاصله این لوله از دیوار مطابق مقررات است؟",
            (True, True, True),
            (True, True),
        ),
        (
            "آیا این clearance violation مطابق ضوابط قابل قبول است؟",
            (True, True, True),
            (True, True),
        ),
        (
            "سلام، حالت چطوره؟",
            (False, False, False),
            (False, False),
        ),
    ],
)
def test_persian_routing_cases(question, llm_decision, expected):
    needs_vector, needs_graph, is_technical = llm_decision
    result = _route_with_llm_decision(
        question,
        {
            "needs_vector": needs_vector,
            "needs_graph": needs_graph,
            "is_technical": is_technical,
            "confidence": 0.9,
            "reasoning": "test decision",
        },
    )

    assert (result["needs_vector"], result["needs_graph"]) == expected


def test_technical_question_sent_to_neither_falls_back_to_vector():
    result = apply_routing_policy(
        "برای نصب این تجهیز چه نکاتی باید رعایت شود؟",
        {
            "needs_vector": False,
            "needs_graph": False,
            "is_technical": True,
            "confidence": 0.45,
            "reasoning": "uncertain",
        },
    )

    assert result["needs_vector"] is True
    assert result["needs_graph"] is False
    assert "conservatively" in result["reasoning"]


def test_graph_only_technical_question_is_not_broadened_to_vector():
    result = apply_routing_policy(
        "فاصله لوله شماره ۱۲ از دیوار چقدر است؟",
        {
            "needs_vector": False,
            "needs_graph": True,
            "is_technical": True,
            "confidence": 0.9,
            "reasoning": "model-specific measurement",
        },
    )

    assert result["needs_vector"] is False
    assert result["needs_graph"] is True


def test_conversation_sent_to_neither_stays_neither():
    result = apply_routing_policy(
        "سلام، حالت چطوره؟",
        {
            "needs_vector": False,
            "needs_graph": False,
            "is_technical": False,
            "confidence": 0.99,
            "reasoning": "greeting",
        },
    )

    assert result["needs_vector"] is False
    assert result["needs_graph"] is False
