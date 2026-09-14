"""Deterministic Cypher templates for high-frequency question patterns.

Checked by GraphRetriever.ask() BEFORE falling through to LLM-based
generation in CypherGenerator. Free-form LLM generation is the least
reliable retrieval path available (it depends on the model correctly
inferring schema quirks like tag-vs-id from a text prompt) - it should
be the fallback for novel phrasing, not the default for well-understood,
high-frequency question shapes.

Every template added here MUST have a corresponding case in
tests/test_graph_retrieval_regression.py - do not add a template
without a regression test that pins its expected output.
"""
import re
from typing import Optional

# 5+ digit numeric IDs - matches the trailing tag embedded in element
# names, e.g. "Basic Wall:MockUp Storage Wall:817660" -> "817660".
_ID_PATTERN = re.compile(r"\b(\d{5,})\b")

_CLASH_KEYWORDS = re.compile(r"\bclash(es|ing)?\b", re.IGNORECASE)
_WALL_COUNT_PATTERN = re.compile(
    r"\bhow many\b.{0,40}\bifcwall\b", re.IGNORECASE
)


def _element_id_match_clause(element_id: str, var: str = "e") -> str:
    """Preferred match clause for a bare numeric element ID.

    Prefers the `tag` property (exact match, precise) when present on
    the node; `name CONTAINS` remains correct as a fallback for graphs
    that haven't been migrated to have `tag` yet (see
    bim_graph/migrate_add_tags.py). Cypher's OR short-circuits per-row,
    so this is safe to use unconditionally even before every node has
    been backfilled with `tag`.
    """
    return f"({var}.tag = '{element_id}' OR {var}.name CONTAINS '{element_id}')"


def try_template_match(question: str) -> Optional[str]:
    """Return a known-good Cypher string if `question` matches a
    supported pattern, else None (caller should fall through to
    CypherGenerator.generate() for novel phrasing).

    Templates here are intentionally conservative: each only fires on
    an unambiguous pattern (explicit keyword + explicit numeric ID).
    A near-miss falls through to the LLM rather than risk a
    confidently-wrong deterministic answer.
    """
    question = question or ""

    # Pattern: "does/is wall|element <id> clash ... doors|windows|..."
    if _CLASH_KEYWORDS.search(question):
        ids = _ID_PATTERN.findall(question)
        if ids:
            element_id = ids[0]
            return (
                "MATCH (e:Element)-[r:CLASHES_WITH]-(other:Element) "
                f"WHERE {_element_id_match_clause(element_id)} "
                "RETURN e.name AS source_name, other.name AS other_name, "
                "other.ifcType AS other_type, r.issue AS issue, "
                "r.metric AS metric, "
                # Identity columns for the 3D viewer. Aliased with the _id/_guid
                # suffixes bim_graph.visualization harvests, and additive to the
                # answer columns above so context formatting is unchanged.
                "e.id AS element_a_id, coalesce(e.ifcGuid, e.id) AS element_a_guid, "
                "e.name AS element_a_name, e.ifcType AS element_a_type, "
                "e.storeyName AS element_a_storey, "
                "other.id AS element_b_id, coalesce(other.ifcGuid, other.id) AS element_b_guid, "
                "other.name AS element_b_name, other.ifcType AS element_b_type, "
                "other.storeyName AS element_b_storey "
                "LIMIT 50"
            )

    # Pattern: "how many IfcWall(s) ... exist"
    if _WALL_COUNT_PATTERN.search(question):
        return "MATCH (w:IfcWall) RETURN count(w) AS wall_count"

    return None
