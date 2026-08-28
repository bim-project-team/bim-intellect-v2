"""Schema-aware plans for common BIM graph question shapes.

The planner intentionally handles shapes where a plausible but incomplete
free-form Cypher query is dangerous (grouped counts, exact type counts and
property-presence statistics). Novel questions still use the LLM generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GraphQueryPlan:
    intent: str
    cypher: str
    parameters: dict = field(default_factory=dict)
    required_columns: tuple[str, ...] = ()
    requested_outputs: tuple[str, ...] = ()
    source: str = "planner"


_IFC_TYPE = re.compile(r"\bIfc[A-Za-z][A-Za-z0-9_]*(?![A-Za-z0-9_])")
_ISSUE = re.compile(
    r"(?:\b(?:CLASH|CLEARANCE_VIOLATION|CLASHES_WITH)\b|تداخل|کلش|نقض\s+فاصله)",
    re.IGNORECASE,
)
_COUNT = re.compile(r"(?:\bcount\b|\bhow many\b|چند|تعداد)", re.IGNORECASE)
_LIST = re.compile(r"(?:\blist\b|فهرست|شناسه|نام.*عنصر)", re.IGNORECASE)
_ANOMALY = re.compile(r"\b(?:anomalyScore|isAnomaly)\b", re.IGNORECASE)
_STOREY = re.compile(
    r"(?:در\s+طبقه|on\s+(?:the\s+)?(?:floor|storey))\s+(.+?)"
    r"(?=\s+(?:چند|چه\s+تعداد|how\s+many|count)|[؟?]|$)",
    re.IGNORECASE,
)


def _unique_ifc_types(question: str) -> list[str]:
    return list(dict.fromkeys(_IFC_TYPE.findall(question or "")))


def _issue_count_plan(question: str) -> GraphQueryPlan | None:
    if not (_ISSUE.search(question) and _COUNT.search(question)):
        return None
    storey_match = _STOREY.search(question)
    where = ""
    parameters = {}
    intent = "issues_by_type"
    if storey_match:
        storey = storey_match.group(1).strip()
        where = "WHERE a.storeyName = $storey OR b.storeyName = $storey "
        parameters["storey"] = storey
        intent = "issues_by_type_on_storey"
    return GraphQueryPlan(
        intent=intent,
        cypher=(
            "MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element) "
            f"{where}"
            "RETURN r.issue AS issue_type, count(r) AS issue_count "
            "ORDER BY issue_type"
        ),
        parameters=parameters,
        required_columns=("issue_type", "issue_count"),
        requested_outputs=("count grouped by CLASHES_WITH.issue",),
    )


def _anomaly_plan(question: str) -> GraphQueryPlan | None:
    if not _ANOMALY.search(question):
        return None
    return GraphQueryPlan(
        intent="element_anomaly_property_counts",
        cypher=(
            "MATCH (e:Element) "
            "RETURN count(CASE WHEN e.anomalyScore IS NOT NULL THEN 1 END) AS scored_count, "
            "count(CASE WHEN e.isAnomaly = true THEN 1 END) AS anomaly_count"
        ),
        required_columns=("scored_count", "anomaly_count"),
        requested_outputs=("anomalyScore presence count", "isAnomaly=true count"),
    )


def _relationship_listing_plan(question: str) -> GraphQueryPlan | None:
    types = _unique_ifc_types(question)
    if len(types) < 2 or not (_ISSUE.search(question) and _LIST.search(question)):
        return None
    parameters = {"type_a": types[0], "type_b": types[1]}
    return GraphQueryPlan(
        intent="typed_clashes_with_listing",
        cypher=(
            "MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element) "
            "WHERE (a.ifcType = $type_a AND b.ifcType = $type_b) "
            "OR (a.ifcType = $type_b AND b.ifcType = $type_a) "
            "RETURN r.issue AS issue_type, r.metric AS metric, "
            "a.name AS element_a_name, a.id AS element_a_id, "
            "b.name AS element_b_name, b.id AS element_b_id "
            # Metric ordering avoids filling the displayed 25-row context
            # with only the alphabetically first issue category.
            "ORDER BY metric DESC LIMIT 50"
        ),
        parameters=parameters,
        required_columns=(
            "issue_type", "metric", "element_a_name", "element_a_id",
            "element_b_name", "element_b_id",
        ),
        requested_outputs=("relationship rows with issue, metric, endpoint names and IDs",),
    )


def _exact_ifc_type_count_plan(question: str) -> GraphQueryPlan | None:
    types = _unique_ifc_types(question)
    if not types or not _COUNT.search(question):
        return None
    parameters = {f"ifc_type_{index}": value for index, value in enumerate(types)}
    projections = [
        f"sum(CASE WHEN e.ifcType = $ifc_type_{index} THEN 1 ELSE 0 END) AS ifc_type_{index}_count"
        for index in range(len(types))
    ]
    columns = tuple(f"ifc_type_{index}_count" for index in range(len(types)))
    return GraphQueryPlan(
        intent="exact_ifc_type_counts",
        cypher="MATCH (e:Element) RETURN " + ", ".join(projections),
        parameters=parameters,
        required_columns=columns,
        requested_outputs=tuple(f"exact count of {value}" for value in types),
    )


def plan_graph_question(question: str) -> GraphQueryPlan | None:
    """Return a complete deterministic plan, or None for free-form fallback."""
    for planner in (
        _anomaly_plan,
        _issue_count_plan,
        _relationship_listing_plan,
        _exact_ifc_type_count_plan,
    ):
        plan = planner(question or "")
        if plan is not None:
            return plan
    return None


def missing_result_columns(records: list[dict], required_columns: tuple[str, ...]) -> list[str]:
    if not required_columns:
        return []
    # An empty list result is a complete answer (zero matches), not evidence
    # that projected columns were omitted. Aggregate queries still return a row.
    if not records:
        return []
    available = set().union(*(record.keys() for record in records)) if records else set()
    return [column for column in required_columns if column not in available]
