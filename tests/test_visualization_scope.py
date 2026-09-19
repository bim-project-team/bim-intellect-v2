"""Tests for resolving which BIM elements a graph answer is about.

These cover the gap that motivated the feature: aggregate answers such as
``count(r)`` name no element, so the viewer would have nothing to highlight for
the most common question shapes unless identity is resolved on a separate axis.

Everything here runs without Neo4j. Cypher is asserted by shape, and the
retriever's fallback path is exercised against a fake client.
"""

from __future__ import annotations

import pytest

from bim_graph import graph_retriever
from bim_graph.cypher_templates import try_template_match
from bim_graph.graph_retriever import GraphRetriever
from bim_graph.query_planner import plan_graph_question
from bim_graph.visualization import (
    MAX_HIGHLIGHT_ELEMENTS, REASON_GRAPH_ELEMENTS, REASON_GRAPH_UNAVAILABLE,
    REASON_NO_EVIDENCE, REASON_RELATED_TYPES, build_payload, harvest_elements,
    looks_like_element_node, subject_ifc_types,
)

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


# ------------------------------------------------------------------
# Identity harvesting
# ------------------------------------------------------------------

def test_harvest_reads_both_endpoints_of_a_relationship_row():
    """A clash is a fact about a pair, so both endpoints must be highlightable."""
    elements = harvest_elements([{
        "issue_type": "CLASH", "metric": 1.5,
        "element_a_id": "p::f::A", "element_a_guid": "guid-a",
        "element_a_name": "Wall A", "element_a_type": "IfcWall",
        "element_a_storey": "Level 5",
        "element_b_id": "p::f::B", "element_b_guid": "guid-b",
        "element_b_name": "Pipe B", "element_b_type": "IfcFlowSegment",
        "element_b_storey": "Level 5",
    }])
    assert [item["ifc_guid"] for item in elements] == ["guid-a", "guid-b"]
    assert elements[0]["ifc_type"] == "IfcWall"
    assert elements[1]["storey_name"] == "Level 5"


def test_harvest_deduplicates_elements_repeated_across_rows():
    """One element in many clashes must be highlighted once, not many times."""
    rows = [
        {"element_a_guid": "guid-a", "element_b_guid": f"guid-{index}"}
        for index in range(3)
    ]
    guids = [item["ifc_guid"] for item in harvest_elements(rows)]
    assert guids.count("guid-a") == 1
    assert len(guids) == 4


def test_harvest_preserves_result_order():
    """Planned queries order by severity, so truncation must keep the worst."""
    rows = [{"element_id": f"e{index}"} for index in range(5)]
    assert [item["element_id"] for item in harvest_elements(rows)] == [f"e{i}" for i in range(5)]


def test_harvest_derives_the_gltf_lookup_key_from_a_federated_graph_id():
    """A query projecting only `id` must still yield a highlightable element.

    extract_graph builds federated ids as "<project>::<file>::<GlobalId>", and
    only the GlobalId names a glTF node. Carrying the composite id through as the
    lookup key would silently match no mesh.
    """
    element = harvest_elements([{"element_id": "proj::model-abc::2JH4bkKkX2AeigVl5N9K8X"}])[0]
    assert element["element_id"] == "proj::model-abc::2JH4bkKkX2AeigVl5N9K8X"
    assert element["ifc_guid"] == "2JH4bkKkX2AeigVl5N9K8X"


def test_harvest_accepts_a_single_file_id_that_is_already_a_guid():
    """Non-federated loads store the bare GlobalId as `id`."""
    element = harvest_elements([{"element_id": "2JH4bkKkX2AeigVl5N9K8X"}])[0]
    assert element["element_id"] == element["ifc_guid"] == "2JH4bkKkX2AeigVl5N9K8X"


def test_harvest_prefers_an_explicit_guid_over_deriving_one():
    element = harvest_elements([{"element_id": "p::f::A", "element_guid": "real-guid"}])[0]
    assert element["element_id"] == "p::f::A"
    assert element["ifc_guid"] == "real-guid"


def test_harvest_reads_unaliased_llm_projections():
    """Free-form Cypher usually does not alias; Neo4j names the column "e.id".

    CYPHER_GENERATOR_PROMPT's own examples end in `RETURN e.id, e.name,
    e.storeyName`, so ignoring this shape would leave the viewer empty for every
    novel question -- the exact path the LLM generator exists to serve.
    """
    elements = harvest_elements([{
        "e.id": "guid-a", "e.name": "Wall A", "e.ifcType": "IfcWall", "e.storeyName": "Level 5",
    }])
    assert elements == [{
        "element_id": "guid-a", "ifc_guid": "guid-a", "name": "Wall A",
        "ifc_type": "IfcWall", "storey_name": "Level 5",
    }]


def test_harvest_keeps_unaliased_variables_as_separate_elements():
    elements = harvest_elements([{
        "e.id": "guid-a", "e.name": "Wall",
        "other.id": "guid-b", "other.ifcType": "IfcDoor",
    }])
    assert [item["ifc_guid"] for item in elements] == ["guid-a", "guid-b"]
    assert elements[1]["ifc_type"] == "IfcDoor"


def test_harvest_does_not_split_one_element_across_generic_alias_prefixes():
    """`element_id` + `ifc_guid` + `name` describe one element, not three.

    Grouping purely by prefix would read those as groups "element", "ifc" and "",
    inventing phantom highlights.
    """
    elements = harvest_elements([{
        "element_id": "p::f::A", "ifc_guid": "guid-a", "name": "Wall A",
        "ifc_type": "IfcWall", "storey_name": "Level 5",
    }])
    assert len(elements) == 1
    assert elements[0]["ifc_guid"] == "guid-a"
    assert elements[0]["storey_name"] == "Level 5"


def test_harvest_reads_a_returned_node_property_map():
    """Neo4jClient.run() flattens driver Nodes to dicts, so this is the real shape."""
    elements = harvest_elements([{"e": {
        "id": "p::f::A", "ifcGuid": "guid-a", "name": "Wall A",
        "ifcType": "IfcWall", "storeyName": "Level 5",
    }}])
    assert elements == [{
        "element_id": "p::f::A", "ifc_guid": "guid-a", "name": "Wall A",
        "ifc_type": "IfcWall", "storey_name": "Level 5",
    }]


def test_harvest_ignores_aggregate_only_and_malformed_rows():
    assert harvest_elements([{"issue_count": 36886}, {"wall_count": 1584}]) == []
    assert harvest_elements([]) == []
    assert harvest_elements([None, "unexpected"]) == []


def test_harvest_drops_null_like_identifier_values():
    """Cypher nulls arrive as None and must not become an unhighlightable entry."""
    assert harvest_elements([{"element_id": None, "element_guid": "none"}]) == []


def test_node_detection_requires_element_property_names():
    assert looks_like_element_node({"id": "x", "ifcType": "IfcWall"})
    assert not looks_like_element_node({"issue": "CLASH", "metric": 1.0})
    assert not looks_like_element_node("IfcWall")


# ------------------------------------------------------------------
# Planner visualization queries
# ------------------------------------------------------------------

def test_aggregate_answer_carries_a_query_that_names_its_elements():
    """The point of the feature: count(r) alone would leave nothing to show."""
    plan = plan_graph_question(ISSUE_COUNTS)
    assert "count(r) AS issue_count" in plan.cypher
    assert not harvest_elements([{"issue_type": "CLASH", "issue_count": 6}])
    assert plan.visualization_cypher
    for column in ("element_a_id", "element_a_guid", "element_b_id", "element_b_guid"):
        assert f" AS {column}" in plan.visualization_cypher


def test_visualization_query_reuses_the_answer_query_filters(monkeypatch):
    """The highlight must be the same element set the count was taken over."""
    plan = plan_graph_question(FLOOR_COUNTS)
    assert plan.intent == "issues_by_type_on_storey"
    assert "a.storeyName = $storey OR b.storeyName = $storey" in plan.visualization_cypher
    # Same parameters, so no second source of truth for the filter value.
    assert "$storey" in plan.cypher and plan.parameters == {"storey": "BLDG. 1,2,3- LEVEL 6 FLR. FIN."}


def test_visualization_queries_are_read_only_and_bounded():
    questions = (ISSUE_COUNTS, FLOOR_COUNTS, ANOMALY_COUNTS, EXACT_TYPES)
    for question in questions:
        plan = plan_graph_question(question)
        cypher = plan.visualization_cypher
        assert cypher, f"expected a visualization query for {plan.intent}"
        assert cypher.startswith("MATCH ")
        assert "LIMIT " in cypher
        for keyword in ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DETACH"):
            assert keyword not in cypher.upper()


def test_anomaly_visualization_shows_flagged_elements_not_the_whole_graph():
    """The scored population is every element; only flagged ones are meaningful."""
    plan = plan_graph_question(ANOMALY_COUNTS)
    assert "e.isAnomaly = true" in plan.visualization_cypher
    assert "ORDER BY e.anomalyScore DESC" in plan.visualization_cypher


def test_exact_type_visualization_reselects_the_same_types():
    plan = plan_graph_question(EXACT_TYPES)
    assert plan.visualization_cypher.count("e.ifcType = $ifc_type_") == 2
    # Parameters are shared with the counting query, so the sets cannot diverge.
    assert plan.parameters == {"ifc_type_0": "IfcPipe", "ifc_type_1": "IfcFlowSegment"}


def test_clash_template_projects_identity_alongside_its_answer_columns():
    """Template answers gain identity without changing what the LLM reads."""
    cypher = try_template_match(
        "Does the wall 'MockUp Storage Wall' (817660) have any clashes with doors?"
    )
    for column in ("source_name", "other_name", "issue", "metric"):
        assert f" AS {column}" in cypher
    for column in ("element_a_id", "element_a_guid", "element_b_id", "element_b_guid"):
        assert f" AS {column}" in cypher
    assert cypher.count("coalesce(") == 2


# ------------------------------------------------------------------
# Subject vocabulary (opt-in view only)
# ------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("What is the minimum landing depth for stairs?", ["IfcStair", "IfcStairFlight"]),
    ("حداقل عمق پاگرد پله چقدر است؟", ["IfcStair", "IfcStairFlight"]),
    # ZWNJ acts as a word boundary, so a compound word still matches.
    ("راه‌پله چه شرایطی دارد؟", ["IfcStair", "IfcStairFlight"]),
    ("الزامات آسانسور چیست؟", ["IfcTransportElement"]),
    ("What clearance is required around doors?", ["IfcDoor"]),
    ("فاصله درها از دیوارها چقدر است؟", ["IfcWall", "IfcDoor"]),
])
def test_subject_vocabulary_maps_bilingual_terms_to_ifc_types(question, expected):
    assert subject_ifc_types(question) == expected


def test_subject_vocabulary_avoids_substring_false_positives():
    """Latin: "outdoor" is not a request to show every door."""
    assert "IfcDoor" not in subject_ifc_types("What are the outdoor lighting rules?")


def test_subject_vocabulary_respects_persian_word_boundaries():
    """Persian: "چقدر" ends in the letters of "در" and must not match doors.

    \\b cannot express this -- Persian letters are word characters, so "\\bدر\\b"
    still matches inside "چقدر". Hence the lookaround on non-word characters.
    """
    assert subject_ifc_types("عمق پاگرد چقدر است؟") == []


def test_subject_vocabulary_returns_nothing_for_a_subjectless_question():
    assert subject_ifc_types("سلام، حالت چطوره؟") == []
    assert subject_ifc_types("") == []


# ------------------------------------------------------------------
# Payload assembly
# ------------------------------------------------------------------

def test_payload_reports_graph_elements_and_their_storey_scenes():
    from bim_graph.scene_export import scene_key

    payload = build_payload("clashes on level 5", {"elements": [
        {"element_id": "A", "ifc_guid": "guid-a", "name": "Wall",
         "ifc_type": "IfcWall", "storey_name": "Level 5"},
    ]}, project_id="proj")
    assert payload["available"] is True
    assert payload["reason"] == REASON_GRAPH_ELEMENTS
    assert payload["project_id"] == "proj"
    assert payload["scenes"] == [scene_key("Level 5")]
    assert payload["truncated"] is False


def test_payload_deduplicates_scenes_across_elements_on_one_storey():
    elements = [
        {"element_id": f"e{i}", "ifc_guid": f"g{i}", "name": "", "ifc_type": "IfcWall",
         "storey_name": "Level 5"}
        for i in range(4)
    ]
    assert len(build_payload("q", {"elements": elements})["scenes"]) == 1


def test_payload_caps_highlights_and_says_so():
    """One question must not ship the whole model to the browser."""
    elements = [
        {"element_id": f"e{i}", "ifc_guid": f"g{i}", "name": "", "ifc_type": "IfcWall",
         "storey_name": "Level 5"}
        for i in range(MAX_HIGHLIGHT_ELEMENTS + 25)
    ]
    payload = build_payload("q", {"elements": elements})
    assert len(payload["highlight"]) == MAX_HIGHLIGHT_ELEMENTS
    assert payload["truncated"] is True


def test_payload_offers_the_opt_in_type_view_without_claiming_evidence():
    """A regulation-only answer verified no elements, so available stays False."""
    payload = build_payload("What is the minimum landing depth for stairs?", None)
    assert payload["available"] is False
    assert payload["reason"] == REASON_RELATED_TYPES
    assert payload["related_types"] == ["IfcStair", "IfcStairFlight"]
    assert payload["highlight"] == []


def test_payload_distinguishes_graph_failure_from_absent_elements():
    """A failed query is not the same claim as "no such elements exist"."""
    failed = build_payload("چند تداخل وجود دارد؟", {"error": "connection refused"})
    assert failed["reason"] == REASON_GRAPH_UNAVAILABLE
    assert failed["available"] is False
    assert failed["related_types"] == []


def test_payload_is_empty_for_a_subjectless_conversational_turn():
    payload = build_payload("", None)
    assert payload["reason"] == REASON_NO_EVIDENCE
    assert payload["available"] is False
    assert payload["related_types"] == []


# ------------------------------------------------------------------
# Retriever integration
# ------------------------------------------------------------------

class _FakeClient:
    """Records queries and replays scripted results, so no DB is required."""

    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def validate(self, query, parameters=None):
        return True, []

    def run(self, query, parameters=None):
        self.executed.append(query)
        return self.results.pop(0) if self.results else []


def test_retriever_runs_the_visualization_query_when_the_answer_is_an_aggregate(monkeypatch):
    client = _FakeClient([
        [{"issue_type": "CLASH", "issue_count": 6}],
        [{"element_a_id": "A", "element_a_guid": "guid-a", "element_a_name": "Wall",
          "element_a_type": "IfcWall", "element_a_storey": "Level 6",
          "element_b_id": "B", "element_b_guid": "guid-b", "element_b_name": "Pipe",
          "element_b_type": "IfcFlowSegment", "element_b_storey": "Level 6"}],
    ])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    result = GraphRetriever().ask(ISSUE_COUNTS)
    assert [item["ifc_guid"] for item in result["elements"]] == ["guid-a", "guid-b"]
    assert len(client.executed) == 2, "expected the answer query plus one identity query"
    assert "count(r) AS issue_count" in client.executed[0]


def test_retriever_skips_the_extra_query_when_results_are_complete(monkeypatch):
    """No identity query is needed when the answer's own rows carry full identity,
    including the storey that scene resolution depends on."""
    client = _FakeClient([[{
        # The planned listing's full contract, so the completeness check passes
        # and the run is not diverted into a corrective query.
        "issue_type": "CLASH", "metric": 2.5,
        "element_a_id": "A", "element_a_guid": "guid-a", "element_a_name": "Pipe",
        "element_a_storey": "Ebene 5",
        "element_b_id": "B", "element_b_guid": "guid-b", "element_b_name": "Beam",
        "element_b_storey": "Ebene 5",
    }]])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    result = GraphRetriever().ask(
        "تداخل‌های بین IfcFlowSegment و IfcBeam را فهرست کن؛ نام و شناسه هر دو عنصر را ذکر کن"
    )
    assert [item["ifc_guid"] for item in result["elements"]] == ["guid-a", "guid-b"]
    assert [item["storey_name"] for item in result["elements"]] == ["Ebene 5", "Ebene 5"]
    assert len(client.executed) == 1, "complete identity; no extra query expected"


def test_partial_identity_is_completed_from_the_plans_identity_query(monkeypatch):
    """Rows that name elements but omit their storey must be completed, not used
    as-is: a blank storey_name resolves every element to the `unassigned` scene
    even though a real storey scene exists (the reported viewer bug)."""
    client = _FakeClient([
        [{
            # Answer rows: full aggregate contract, element identity present,
            # storey absent -- the reported bug's precondition.
            "issue_type": "CLASH", "issue_count": 1, "metric": 8.3768,
            "element_a_id": "default-project::model_0_arc-x::1$DZROIQX0RgNTaHr7NfS$",
            "element_a_guid": "1$DZROIQX0RgNTaHr7NfS$", "element_a_name": "Roof",
            "element_b_id": "default-project::model_0_structure-x::39hMvtcAHFA8CweEUbIYMM",
            "element_b_guid": "39hMvtcAHFA8CweEUbIYMM", "element_b_name": "Slab 0.1500",
        }],
        [{
            # The plan's identity query: same elements, storey projected.
            "element_a_id": "default-project::model_0_arc-x::1$DZROIQX0RgNTaHr7NfS$",
            "element_a_guid": "1$DZROIQX0RgNTaHr7NfS$", "element_a_name": "Roof",
            "element_a_type": "IfcSlab", "element_a_storey": "Ebene 5",
            "element_b_id": "default-project::model_0_structure-x::39hMvtcAHFA8CweEUbIYMM",
            "element_b_guid": "39hMvtcAHFA8CweEUbIYMM", "element_b_name": "Slab 0.1500",
            "element_b_type": "IfcSlab", "element_b_storey": "Storey",
        }],
    ])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    result = GraphRetriever().ask(ISSUE_COUNTS)
    assert [item["storey_name"] for item in result["elements"]] == ["Ebene 5", "Storey"]
    assert len(client.executed) == 2, "identity query must run to fill the missing storey"
    # Answer-row values win over the identity query's; nothing was lost.
    assert result["elements"][0]["name"] == "Roof"


def test_merge_element_records_fills_blanks_without_disturbing_primary():
    from bim_graph.visualization import merge_element_records
    primary = [
        {"element_id": "A", "ifc_guid": "g-a", "name": "Wall A", "ifc_type": "IfcWall", "storey_name": ""},
        {"element_id": "B", "ifc_guid": "g-b", "name": "", "ifc_type": "", "storey_name": "Stair"},
    ]
    secondary = [
        # Extra element not in primary must be ignored, not appended.
        {"element_id": "C", "ifc_guid": "g-c", "name": "Other", "ifc_type": "IfcDoor", "storey_name": "Attic"},
        {"element_id": "A2", "ifc_guid": "g-a", "name": "ignored-name", "ifc_type": "Ignored", "storey_name": "Ebene 5"},
        {"element_id": "B2", "ifc_guid": "g-b", "name": "Beam B", "ifc_type": "IfcBeam", "storey_name": "Ignored"},
    ]
    merged = merge_element_records(primary, secondary)
    assert [item["ifc_guid"] for item in merged] == ["g-a", "g-b"]
    assert merged[0]["storey_name"] == "Ebene 5" and merged[0]["name"] == "Wall A"
    assert merged[0]["ifc_type"] == "IfcWall", "primary non-empty values must win"
    assert merged[1]["name"] == "Beam B" and merged[1]["storey_name"] == "Stair"


def test_element_resolution_failure_does_not_fail_the_answer(monkeypatch):
    """The answer is the product; the highlight is a presentation of it."""
    class Failing(_FakeClient):
        def validate(self, query, parameters=None):
            # Passes for the answer query, refuses the identity query.
            return ("count(r)" in query), ["visualization query rejected"]

    client = Failing([[{"issue_type": "CLASH", "issue_count": 6}]])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    result = GraphRetriever().ask(ISSUE_COUNTS)
    assert result["record_count"] == 1
    assert result["elements"] == []


def test_graph_sources_and_highlights_come_from_the_same_harvest(monkeypatch):
    """Citation chips and highlighted geometry cannot disagree about elements."""
    rows = [{"e": {"id": "p::f::A", "ifcGuid": "guid-a", "name": "Wall A",
                   "ifcType": "IfcWall", "storeyName": "Level 5"}}]
    client = _FakeClient([rows])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    retriever = GraphRetriever()
    # This phrasing matches no template or planner, so it exercises the free-form
    # path. The generator is stubbed because the assertion is about how a returned
    # node is turned into sources and highlights, not about Cypher generation --
    # and a live LLM call would make the test network-dependent.
    monkeypatch.setattr(
        retriever.cypher_gen, "generate",
        lambda question: "MATCH (e:IfcWall) RETURN e LIMIT 50",
    )
    result = retriever.ask("show me wall details")
    assert [source["ifc_guid"] for source in result["sources"]] == ["guid-a"]
    assert [item["ifc_guid"] for item in result["elements"]] == ["guid-a"]
    # The formatted context must expose the identifiers, not a truncated dict.
    assert "ifcGuid=guid-a" in result["context"]
    assert "name=Wall A" in result["context"]


def test_returned_relationships_are_rendered_readably(monkeypatch):
    """Record.data() renders a relationship as (start_props, type, end_props).

    Formatting it as a bare tuple would hit the 100-character scalar truncation
    and hide both endpoints from the model.
    """
    rows = [{"r": ({"name": "Wall A"}, "CLASHES_WITH", {"name": "Pipe B"})}]
    client = _FakeClient([rows])
    monkeypatch.setattr(graph_retriever, "Neo4jClient", lambda: client)
    retriever = GraphRetriever()
    monkeypatch.setattr(
        retriever.cypher_gen, "generate",
        lambda question: "MATCH ()-[r:CLASHES_WITH]->() RETURN r LIMIT 50",
    )
    result = retriever.ask("show me a clash relationship")
    assert "[:CLASHES_WITH](Wall A\u2192Pipe B)" in result["context"]
