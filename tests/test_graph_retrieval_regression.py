"""Regression suite for graph retrieval accuracy. Run this after ANY
change to CYPHER_GENERATOR_PROMPT, cypher_templates.py, or the graph
ingestion/schema. A failure here means a previously-working question
type has regressed - do not merge until it passes.

Live expectations are read independently from Neo4j because ingestion
replaces the graph and fixed counts become stale. Query-shape behavior is
pinned separately in deterministic unit tests.

Requires a live Neo4j connection (bim_graph.config) pointed at the
dataset these numbers were verified against. Skips gracefully if no
connection is available, rather than failing CI on an environment
that simply doesn't have the DB configured.
"""

import pytest

from bim_graph.graph_retriever import GraphRetriever
from bim_graph.neo4j_client import Neo4jClient

REGRESSION_CASES = [
    {
        "name": "wall_817660_clashes_with_doors",
        "question": "Does the wall 'MockUp Storage Wall' (817660) have any clashes with doors?",
    },
    {
        "name": "ifcwall_total_count",
        "question": "How many nodes with label IfcWall exist in the building graph?",
    },
]


@pytest.fixture(scope="module")
def graph_retriever():
    try:
        with Neo4jClient() as client:
            client.verify_connectivity()
    except Exception as exc:
        pytest.skip(f"No live Neo4j connection available: {exc}")
    return GraphRetriever()


def _extract_element_ids(context: str) -> set[str]:
    """Best-effort scrape of element ids out of the formatted context
    string produced by GraphRetriever._format_records(). Looks for
    'id=<value>' tokens."""
    import re
    return set(re.findall(r'id=([\w-]+)', context))


def test_wall_817660_clashes_with_doors(graph_retriever):
    case = next(c for c in REGRESSION_CASES if c["name"] == "wall_817660_clashes_with_doors")
    result = graph_retriever.ask(case["question"])

    with Neo4jClient() as client:
        expected = client.run(
            "MATCH (e:Element)-[r:CLASHES_WITH]-(other:Element) "
            "WHERE e.tag = $tag OR e.name CONTAINS $tag RETURN count(r) AS n",
            {"tag": "817660"},
        )[0]["n"]
    assert result["record_count"] == min(expected, 50)
    assert result["cypher_source"] == "template"


def test_ifcwall_total_count(graph_retriever):
    case = next(c for c in REGRESSION_CASES if c["name"] == "ifcwall_total_count")
    result = graph_retriever.ask(case["question"])

    with Neo4jClient() as client:
        expected_count = client.run("MATCH (w:IfcWall) RETURN count(w) AS n")[0]["n"]
    assert str(expected_count) in result["context"], (
        f"Expected current IfcWall count of {expected_count} to appear in the "
        f"result context, but it didn't. Got context: {result['context']!r}. "
        f"Cypher used: {result['cypher_query']}"
    )


def test_wall_tag_template_shortcut_used():
    """The clash-lookup template should fire for this exact phrasing
    without needing an LLM call at all - verifies the template layer
    independently of DB/LLM availability."""
    from bim_graph.cypher_templates import try_template_match

    cypher = try_template_match(
        "Does the wall 'MockUp Storage Wall' (817660) have any clashes with doors?"
    )
    assert cypher is not None, "Expected the clash template to match this question."
    assert "e.tag = '817660'" in cypher
    assert "w.id" not in cypher.replace(" ", "")


def test_cypher_generator_prefers_tag_over_id_for_novel_phrasing():
    """P0 acceptance test: for phrasing that does NOT hit the template
    layer (so it genuinely exercises CYPHER_GENERATOR_PROMPT), the LLM
    must not match a bare numeric ID against `id`. Requires LLM
    credentials; skips if unavailable rather than failing CI on a
    misconfigured environment."""
    from bim_graph.cypher_generator import CypherGenerator
    from rag.openrouter_client import LLMConfigError

    try:
        gen = CypherGenerator()
        cypher = gen.generate(
            "I'm curious whether element number 817660 has been flagged "
            "for interference with anything nearby."
        )
    except LLMConfigError as exc:
        pytest.skip(f"No LLM credentials configured: {exc}")

    compact = cypher.replace(" ", "")
    assert "w.id" not in compact and ".id='817660'" not in compact.replace('"', "'"), (
        f"Generated Cypher matched a numeric ID against `id` instead of `tag`: {cypher}"
    )
    assert "817660" in cypher
