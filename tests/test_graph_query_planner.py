from __future__ import annotations

import pytest

from bim_graph import graph_retriever
from bim_graph.graph_retriever import GraphRetriever
from bim_graph.query_planner import missing_result_columns, plan_graph_question
from rag.orchestrator import apply_routing_policy


ISSUE_COUNTS = (
    "در کل مدل چند رابطه CLASH و چند رابطه CLEARANCE_VIOLATION ثبت شده است؟ "
    "تعداد هر نوع را جداگانه و فقط از روابط CLASHES_WITH گزارش کن"
)
FLOOR_COUNTS = (
    "در طبقه BLDG. 1,2,3- LEVEL 6 FLR. FIN. چند CLASH و چند "
    "CLEARANCE_VIOLATION وجود دارد؟ هر رابطه‌ای را که حداقل یکی از دو عنصر آن در این طبقه است بشمار"
)
ANOMALY_COUNTS = (
    "آیا برای عناصر مدل امتیاز anomalyScore یا وضعیت isAnomaly ثبت شده است؟ "
    "تعداد عناصر امتیازدهی‌شده و تعداد عناصر anomaly را فقط از داده فعلی Neo4j گزارش کن"
)
EXACT_TYPES = (
    "آیا گره‌ای با نوع دقیق IfcPipe در مدل وجود دارد؟ اگر وجود ندارد، تعداد "
    "IfcFlowSegmentها را هم گزارش کن و این دو نوع را با هم اشتباه نگیر"
)
DETAILED_LIST = (
    "تداخل‌ها و نقض فاصله‌های ثبت‌شده بین قطعات مسیر جریان (IfcFlowSegment) و تیرها "
    "(IfcBeam) را فهرست کن؛ نوع مسئله، مقدار metric، نام و شناسه هر دو عنصر را ذکر کن"
)


def test_issue_aggregate_plan_groups_instead_of_returning_one_total():
    plan = plan_graph_question(ISSUE_COUNTS)
    assert plan.intent == "issues_by_type"
    assert "r.issue AS issue_type" in plan.cypher
    assert "count(r) AS issue_count" in plan.cypher
    assert "RETURN count(r)" not in plan.cypher
    assert plan.required_columns == ("issue_type", "issue_count")


def test_floor_issue_plan_uses_either_endpoint_once_and_groups():
    plan = plan_graph_question(FLOOR_COUNTS)
    assert plan.intent == "issues_by_type_on_storey"
    assert "-[r:CLASHES_WITH]->" in plan.cypher
    assert "a.storeyName = $storey OR b.storeyName = $storey" in plan.cypher
    assert "r.issue AS issue_type" in plan.cypher
    assert plan.parameters["storey"] == "BLDG. 1,2,3- LEVEL 6 FLR. FIN."


def test_anomaly_plan_queries_both_requested_properties():
    plan = plan_graph_question(ANOMALY_COUNTS)
    assert "e.anomalyScore IS NOT NULL" in plan.cypher
    assert "e.isAnomaly = true" in plan.cypher
    assert plan.required_columns == ("scored_count", "anomaly_count")


def test_exact_ifc_types_are_distinct_parameterized_counts():
    plan = plan_graph_question(EXACT_TYPES)
    assert plan.intent == "exact_ifc_type_counts"
    assert plan.parameters == {"ifc_type_0": "IfcPipe", "ifc_type_1": "IfcFlowSegment"}
    assert plan.cypher.count("e.ifcType = $") == 2
    assert "CONTAINS" not in plan.cypher


def test_detailed_listing_preserves_all_requested_relationship_fields():
    plan = plan_graph_question(DETAILED_LIST)
    assert plan.intent == "typed_clashes_with_listing"
    assert plan.parameters == {"type_a": "IfcFlowSegment", "type_b": "IfcBeam"}
    for column in (
        "issue_type", "metric", "element_a_name", "element_a_id",
        "element_b_name", "element_b_id",
    ):
        assert column in plan.required_columns
        assert f" AS {column}" in plan.cypher
    assert "LIMIT 50" in plan.cypher
    assert "ORDER BY metric DESC" in plan.cypher
    assert "ORDER BY issue_type" not in plan.cypher


@pytest.mark.parametrize("question", [ISSUE_COUNTS, FLOOR_COUNTS, ANOMALY_COUNTS, EXACT_TYPES, DETAILED_LIST])
def test_explicit_graph_questions_override_incorrect_vector_routing(question):
    routed = apply_routing_policy(question, {
        "needs_vector": True, "needs_graph": False, "is_technical": True,
    })
    assert routed["needs_graph"] is True
    assert routed["needs_vector"] is False


def test_completeness_validator_detects_partial_aggregate_shape():
    assert missing_result_columns(
        [{"total_count": 36886}], ("issue_type", "issue_count")
    ) == ["issue_type", "issue_count"]
    assert missing_result_columns([], ("element_a_id",)) == []


def test_retriever_executes_planned_parameterized_query_and_reports_completeness(monkeypatch):
    captured = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def validate(self, query, parameters=None):
            captured.update(validated=query, validation_parameters=parameters)
            return True, []

        def run(self, query, parameters=None):
            captured.update(executed=query, execution_parameters=parameters)
            return [{"ifc_type_0_count": 0, "ifc_type_1_count": 2261}]

    monkeypatch.setattr(graph_retriever, "Neo4jClient", FakeClient)
    result = GraphRetriever().ask(EXACT_TYPES)
    assert result["cypher_source"] == "planner"
    assert result["graph_intent"] == "exact_ifc_type_counts"
    assert result["completeness_valid"] is True
    assert result["returned_columns"] == ["ifc_type_0_count", "ifc_type_1_count"]
    assert captured["validation_parameters"] == captured["execution_parameters"]


def test_incomplete_result_triggers_exactly_one_corrective_query(monkeypatch):
    class FakeClient:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def validate(self, query, parameters=None):
            return True, []

        def run(self, query, parameters=None):
            self.calls += 1
            if self.calls == 1:
                return [{"total_count": 10}]
            return [
                {"issue_type": "CLASH", "issue_count": 6},
                {"issue_type": "CLEARANCE_VIOLATION", "issue_count": 4},
            ]

    monkeypatch.setattr(graph_retriever, "Neo4jClient", FakeClient)
    retriever = GraphRetriever()
    monkeypatch.setattr(
        retriever.cypher_gen, "generate_completion",
        lambda question, previous_query, missing: (
            "MATCH ()-[r:CLASHES_WITH]->() "
            "RETURN r.issue AS issue_type, count(r) AS issue_count"
        ),
    )
    result = retriever.ask(ISSUE_COUNTS)
    assert result["query_count"] == 2
    assert result["follow_up_query_required"] is True
    assert result["completeness_valid"] is True
    assert result["missing_outputs"] == []
