"""Resolves which BIM elements a graph answer is about, for 3D display.

The problem this solves: the answer to "how many clashes are on Level 5?" is
``count(r)``, which names no element at all. Without a separate resolution step
the viewer would have nothing to highlight for exactly the questions users ask
most. So identity is collected on a second, deterministic axis rather than
inferred from the answer text.

Three sources of element identity, in descending confidence:

1. ``GraphQueryPlan.visualization_cypher`` -- a hand-written parameterized query
   paired with the answer query and reusing its filters. Correct by construction.
2. Identity columns projected by the executed query itself (``*_id``/``*_guid``
   aliases, or whole-node returns). Free when present.
3. Nothing. The answer is reported without a highlight, and the caller may offer
   the opt-in type view below.

Nothing here asks a language model which elements matter. A model-authored
element list would be an unverified claim about the building, which is the class
of output this platform validates rather than trusts.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Bounded so one question cannot ship the whole model to the browser. Highlights
# beyond this point are visually indistinguishable anyway.
MAX_HIGHLIGHT_ELEMENTS = 500

REASON_GRAPH_ELEMENTS = "graph_elements"
REASON_RELATED_TYPES = "related_types"
REASON_NO_EVIDENCE = "no_evidence"
REASON_GRAPH_UNAVAILABLE = "graph_unavailable"

# Two column shapes carry identity, because two Cypher sources produce them:
#
# - Planned and templated queries alias explicitly: element_a_id, element_a_guid.
# - Free-form LLM Cypher usually does not alias at all. CYPHER_GENERATOR_PROMPT's
#   own examples end in `RETURN e.id, e.name, e.storeyName`, and Neo4j names such
#   a column after its expression text -- so the key arrives as "e.id". Ignoring
#   that shape would leave the viewer empty for every novel question, which is
#   exactly the path the LLM generator exists to serve.
#
# Both are normalized to the same (prefix, field) pair below.
_ALIAS_FIELDS = {
    "id": "id", "guid": "guid", "name": "name", "type": "type",
    "storey": "storey", "storey_name": "storey",
}
_PROPERTY_FIELDS = {
    "id": "id", "ifcguid": "guid", "name": "name", "ifctype": "type",
    "storeyname": "storey",
}

# Prefixes that describe the *kind* of identifier rather than which element it
# belongs to. Without this, a single-element projection such as
# ``RETURN e.id AS element_id, e.ifcGuid AS ifc_guid, e.name AS name`` would be
# grouped as three different elements ("element", "ifc", "") and reported as
# phantom highlights. Endpoint prefixes like "element_a" are not in this set and
# stay separate, which is what keeps a clash's two ends distinct.
_GENERIC_GROUPS = frozenset({"", "element", "ifc", "node"})

# Separator used by extract_graph._node_id to build a federated graph identity as
# "<project>::<file>::<GlobalId>". Only the trailing segment is an IFC GlobalId,
# and only a GlobalId matches a glTF node name.
_COMPOSITE_ID_SEPARATOR = "::"

# Element property names as stored by bim_graph.load_to_neo4j, used to recognize
# a whole node returned as a property map.
_NODE_KEYS = {"id", "ifcGuid", "name", "ifcType", "storeyName"}

# Subject vocabulary -> IFC types, used ONLY for the opt-in "related elements of
# this type" view offered when an answer carries no element evidence. Explicit
# and reviewable on purpose: this is orientation, never presented as evidence.
# Bilingual because the assistant answers in Persian by default.
#
# Terms are matched on word boundaries, and ambiguous short words are omitted
# rather than risk a wrong offer. Notably the bare Persian "در" is excluded: it
# is both "door" and the preposition "in", and it occurs as a substring of
# everyday words such as "چقدر" -- so doors are matched via "درها"/"درب" only.
SUBJECT_IFC_TYPES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("stair", "stairs", "stairway", "پله", "پلکان", "راه‌پله", "راه پله"),
     ("IfcStair", "IfcStairFlight")),
    (("elevator", "lift", "آسانسور", "بالابر"), ("IfcTransportElement",)),
    (("wall", "walls", "دیوار", "دیوارها", "دیواره"), ("IfcWall",)),
    (("door", "doors", "درها", "درب", "درهای"), ("IfcDoor",)),
    (("window", "windows", "پنجره", "پنجره‌ها"), ("IfcWindow",)),
    (("column", "columns", "ستون", "ستون‌ها"), ("IfcColumn",)),
    (("beam", "beams", "تیر", "تیرها"), ("IfcBeam",)),
    (("slab", "slabs", "floor slab", "دال", "سقف"), ("IfcSlab",)),
    (("roof", "بام", "پشت‌بام"), ("IfcRoof",)),
    (("railing", "handrail", "نرده", "دست‌انداز"), ("IfcRailing",)),
    (("space", "room", "rooms", "فضا", "اتاق"), ("IfcSpace",)),
    (("duct", "ducts", "کانال", "داکت"), ("IfcFlowSegment",)),
    (("pipe", "pipes", "piping", "لوله", "لوله‌کشی"), ("IfcFlowSegment",)),
    (("fitting", "fittings", "اتصالات"), ("IfcFlowFitting",)),
    (("terminal", "diffuser", "fixture", "ترمینال", "دریچه"), ("IfcFlowTerminal",)),
    (("valve", "damper", "شیر", "دمپر"), ("IfcFlowController",)),
    (("curtain wall", "دیوار پرده‌ای"), ("IfcCurtainWall",)),
    (("covering", "ceiling", "پوشش", "کفپوش"), ("IfcCovering",)),
)

# Compiled once: this runs on every answered question.
#
# A plain \b boundary is wrong for Persian, because \b is defined against \w and
# Persian letters are \w -- "\bدر\b" still matches inside "چقدر". Asserting a
# non-word character on both sides works for both scripts. ZWNJ is deliberately
# left out of \w, so it acts as a boundary and "پله" matches inside "راه‌پله".
_SUBJECT_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(rf"(?<!\w)(?:{'|'.join(re.escape(term) for term in terms)})(?!\w)"), ifc_types)
    for terms, ifc_types in SUBJECT_IFC_TYPES
)


def _clean(value: Any) -> str:
    """Normalize a Cypher scalar to a trimmed string, dropping null-ish values."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "null", "nan"} else text


def _split_column(column: str) -> tuple[str, str] | None:
    """Normalize a result column to ``(group, field)``, or None if not identity.

    Handles both Cypher shapes with one rule set:

    - ``element_a_guid`` -> ("element_a", "guid")   explicit planner/template alias
    - ``e.ifcGuid``      -> ("e", "guid")           unaliased LLM projection
    - ``ifc_guid``       -> ("", "guid")            single-element alias

    Grouping by prefix is what keeps a clash's two endpoints separate instead of
    collapsing them into one element with mixed properties.
    """
    variable, separator, property_name = column.partition(".")
    if separator:
        field = _PROPERTY_FIELDS.get(property_name.casefold())
        return (variable, field) if field else None

    lowered = column.casefold()
    # Longest suffix first: "_storey_name" must not be read as "_name".
    for suffix in sorted(_ALIAS_FIELDS, key=len, reverse=True):
        if lowered == suffix:
            return "", _ALIAS_FIELDS[suffix]
        if lowered.endswith(f"_{suffix}"):
            return column[: -(len(suffix) + 1)], _ALIAS_FIELDS[suffix]
    return None


def _element_from_fields(fields: dict[str, str]) -> dict[str, str] | None:
    """Build one element from collected identity fields, or None if unidentified."""
    element_id = fields.get("id", "")
    ifc_guid = fields.get("guid", "")
    if not (element_id or ifc_guid):
        return None
    if not ifc_guid:
        # A federated graph id is "<project>::<file>::<GlobalId>"; only the last
        # segment names a glTF node. Deriving it here means a query that projects
        # id alone still produces a highlightable element instead of one whose
        # lookup key silently never matches any mesh.
        ifc_guid = element_id.rsplit(_COMPOSITE_ID_SEPARATOR, 1)[-1]
    return {
        # ifcGuid is what glTF node names carry, so it is the viewer's lookup
        # key; element_id remains the graph's own identity.
        "element_id": element_id or ifc_guid,
        "ifc_guid": ifc_guid,
        "name": fields.get("name", ""),
        "ifc_type": fields.get("type", ""),
        "storey_name": fields.get("storey", ""),
    }



def looks_like_element_node(value: Any) -> bool:
    """True for a returned node's property map.

    Neo4jClient.run() calls Record.data(), which converts driver Node objects
    into plain dicts and discards their labels, so structural duck-typing on the
    known Element property names is the only signal that survives.
    """
    return isinstance(value, dict) and bool(_NODE_KEYS & value.keys())


def harvest_elements(records: Iterable[dict]) -> list[dict[str, str]]:
    """Collect distinct elements named by query results, preserving row order.

    Order matters: planned listings sort by severity (``ORDER BY metric DESC``),
    so truncation at the highlight cap keeps the worst offenders rather than an
    arbitrary slice.
    """
    elements: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(element: dict[str, str] | None) -> None:
        if element is None or element["ifc_guid"] in seen:
            return
        seen.add(element["ifc_guid"])
        elements.append(element)

    for record in records or []:
        if not isinstance(record, dict):
            continue
        # Ordered so endpoints are emitted in the order their columns appear.
        groups: dict[str, dict[str, str]] = {}
        for column, value in record.items():
            if looks_like_element_node(value):
                add(_element_from_fields({
                    field: _clean(value.get(key))
                    for key, field in (("id", "id"), ("ifcGuid", "guid"), ("name", "name"),
                                       ("ifcType", "type"), ("storeyName", "storey"))
                }))
                continue
            split = _split_column(column)
            if split is None:
                continue
            group, field = split
            if group.casefold() in _GENERIC_GROUPS:
                group = ""
            cleaned = _clean(value)
            if cleaned:
                groups.setdefault(group, {}).setdefault(field, cleaned)
        for fields in groups.values():
            add(_element_from_fields(fields))
    return elements


def subject_ifc_types(question: str) -> list[str]:
    """IFC types matching the question's subject vocabulary, order preserved.

    Used only to populate the opt-in "related elements" offer, never to justify a
    highlight, so a miss is cheap and a false positive is the thing to avoid.
    """
    text = (question or "").casefold()
    matched: list[str] = []
    for pattern, ifc_types in _SUBJECT_PATTERNS:
        if pattern.search(text):
            matched.extend(value for value in ifc_types if value not in matched)
    return matched


def scene_keys_for(elements: Iterable[dict[str, str]]) -> list[str]:
    """Scene keys covering the storeys of the given elements.

    Imported locally: scene_export pulls in IfcOpenShell lazily, and keeping the
    import here means the retrieval path never needs a compiled geometry stack
    just to name a scene.
    """
    from bim_graph.scene_export import scene_key

    keys: list[str] = []
    for element in elements:
        key = scene_key(element.get("storey_name"))
        if key not in keys:
            keys.append(key)
    return keys


def build_payload(
    question: str,
    graph_result: dict | None,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the ``visualization`` block returned alongside an answer.

    ``reason`` states plainly why there is or is not something to show, so the
    UI never has to infer intent from an empty list:

    - ``graph_elements``     -- specific elements were identified; highlight them
    - ``related_types``      -- no element evidence, but the subject maps to IFC
                                types the user may opt into viewing
    - ``no_evidence``        -- nothing to show, and nothing to offer
    - ``graph_unavailable``  -- the graph was consulted and failed; distinct from
                                the graph legitimately returning no elements
    """
    payload: dict[str, Any] = {
        "available": False,
        "reason": REASON_NO_EVIDENCE,
        "project_id": project_id,
        "highlight": [],
        "scenes": [],
        "related_types": [],
        "truncated": False,
    }

    if graph_result and graph_result.get("error"):
        payload["reason"] = REASON_GRAPH_UNAVAILABLE
        return payload

    elements = list((graph_result or {}).get("elements") or [])
    if elements:
        payload["truncated"] = len(elements) > MAX_HIGHLIGHT_ELEMENTS
        elements = elements[:MAX_HIGHLIGHT_ELEMENTS]
        payload.update(
            available=True,
            reason=REASON_GRAPH_ELEMENTS,
            highlight=elements,
            scenes=scene_keys_for(elements),
        )
        return payload

    related = subject_ifc_types(question)
    if related:
        # available=False deliberately: this is an offer the user must accept,
        # not evidence the answer relied on.
        payload.update(reason=REASON_RELATED_TYPES, related_types=related)
    return payload
