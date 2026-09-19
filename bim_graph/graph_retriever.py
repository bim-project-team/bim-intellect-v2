"""Retrieves and formats graph data from Neo4j for RAG context.

End-to-end flow: natural language -> Cypher -> Neo4j execution ->
human-readable context string + structured source metadata.

Cypher comes from one of two sources, checked in order:
  1. cypher_templates.try_template_match() - deterministic, hand-verified
     templates for high-frequency question shapes (e.g. clash lookups by
     element ID). Preferred whenever a question matches a known pattern,
     since it removes the LLM's chance to mis-infer schema quirks like
     tag-vs-id (see CYPHER_ACCURACY_ROADMAP.md, Section 3.2).
  2. CypherGenerator.generate() - free-form LLM generation, used as the
     fallback for questions that don't match a known template.
"""

import logging
import json
import re
from typing import Any, Dict, List, Optional

from bim_graph.cypher_generator import CypherGenerator
from bim_graph.cypher_templates import try_template_match
from bim_graph.neo4j_client import Neo4jClient
from bim_graph.query_planner import GraphQueryPlan, missing_result_columns, plan_graph_question
from bim_graph.visualization import (
    harvest_elements, looks_like_element_node, merge_element_records,
)

logger = logging.getLogger("bim_intellect.graph_retriever")

# Used only to extract a label for the "is this a bad filter or genuinely
# no data" empty-result check in _format_records() - never used to build
# an executable query from unsanitized input.
_LABEL_IN_QUERY = re.compile(r":(Ifc\w+)")


class GraphRetriever:
    """Graph RAG: question -> Cypher -> execution -> formatted LLM context."""

    def __init__(self):
        self.cypher_gen = CypherGenerator()

    def _resolve_cypher(self, question: str) -> tuple[str, str, GraphQueryPlan | None]:
        """Return (cypher, source) where source is 'template' or 'llm', so
        callers/logs can tell which path produced a given query - useful
        when auditing accuracy, since template-sourced answers should
        essentially never be wrong."""
        template_cypher = try_template_match(question)
        if template_cypher is not None:
            logger.info("Using template-matched Cypher for question: %r", question[:120])
            return template_cypher, "template", None

        plan = plan_graph_question(question)
        if plan is not None:
            logger.info(
                "Graph plan: intent=%s requested_outputs=%s required_columns=%s parameters=%s",
                plan.intent, plan.requested_outputs, plan.required_columns, plan.parameters,
            )
            return plan.cypher, plan.source, plan

        return self.cypher_gen.generate(question), "llm", None

    def ask(self, question: str) -> Dict[str, Any]:
        """Generate Cypher, run it, and format results for LLM consumption."""
        cypher, cypher_source, plan = self._resolve_cypher(question)
        parameters = plan.parameters if plan else {}
        executed_queries = [cypher]
        follow_up_required = False
        elements: List[Dict[str, Any]] = []

        try:
            with Neo4jClient() as client:
                is_valid, warnings = client.validate(cypher, parameters)
                if not is_valid:
                    # The query didn't even plan - almost certainly a bug
                    # in generation, not "no data". Surface this distinctly
                    # rather than letting it fall through to a silent
                    # empty-result execution attempt.
                    logger.error(
                        "Generated Cypher failed validation (source=%s): %s | Query: %s",
                        cypher_source, warnings, cypher[:200],
                    )
                    raise ValueError(f"Generated Cypher failed validation: {warnings}")
                elif warnings:
                    logger.warning(
                        "Cypher planner warnings (source=%s) for %r: %s",
                        cypher_source, cypher[:200], warnings,
                    )

                records = client.run(cypher, parameters)
                returned_columns = sorted(set().union(*(row.keys() for row in records))) if records else []
                missing = missing_result_columns(records, plan.required_columns if plan else ())
                logger.info(
                    "Graph query result: rows=%d columns=%s completeness=%s missing=%s",
                    len(records), returned_columns, "complete" if not missing else "incomplete", missing,
                )
                if missing:
                    follow_up_required = True
                    logger.warning(
                        "Graph completeness repair required: missing=%s; executing one bounded follow-up",
                        missing,
                    )
                    repaired = self.cypher_gen.generate_completion(question, cypher, missing)
                    is_valid, warnings = client.validate(repaired, parameters)
                    if not is_valid:
                        raise ValueError(f"Corrective Cypher failed validation: {warnings}")
                    if warnings:
                        logger.warning("Corrective Cypher planner warnings: %s", warnings)
                    records = client.run(repaired, parameters)
                    executed_queries.append(repaired)
                    cypher = repaired
                    returned_columns = sorted(set().union(*(row.keys() for row in records))) if records else []
                    missing = missing_result_columns(records, plan.required_columns if plan else ())
                    logger.info(
                        "Graph follow-up result: rows=%d columns=%s completeness=%s missing=%s",
                        len(records), returned_columns, "complete" if not missing else "incomplete", missing,
                    )
                    if missing:
                        raise RuntimeError(
                            "Corrective graph query remained incomplete; missing: " + ", ".join(missing)
                        )
                context, sources = self._format_records(records, cypher, client)
                if plan:
                    context = (
                        f"Graph intent: {plan.intent}\n"
                        f"Requested outputs: {json.dumps(plan.requested_outputs, ensure_ascii=False)}\n"
                        f"Query parameters: {json.dumps(parameters, ensure_ascii=False)}\n"
                        f"Completeness validation: complete\n{context}"
                    )
                elements = self._resolve_elements(records, plan, parameters, client)
        except Exception as exc:
            logger.error("Cypher execution failed: %s | Query: %s", exc, cypher[:200])
            raise RuntimeError(f"Graph query execution failed: {exc}") from exc

        return {
            "cypher_query": cypher,
            "cypher_source": cypher_source,
            "query_parameters": parameters,
            "graph_intent": plan.intent if plan else "free_form",
            "requested_outputs": list(plan.requested_outputs) if plan else [],
            "returned_columns": returned_columns,
            "completeness_valid": not missing,
            "missing_outputs": missing,
            "query_count": len(executed_queries),
            "follow_up_query_required": follow_up_required,
            "cypher_queries": executed_queries,
            "context": context,
            "sources": sources,
            "elements": elements,
            "record_count": len(records),
        }

    def _resolve_elements(
        self,
        records: List[Dict],
        plan: GraphQueryPlan | None,
        parameters: Dict[str, Any],
        client: Neo4jClient,
    ) -> List[Dict[str, Any]]:
        """Identify the elements the answer is about, for the 3D viewer.

        Prefers identities already present in the answer's own result rows. When
        they are incomplete -- aggregate answers (`count(r)`) name no element at
        all, and free-form rows often name elements without their storey -- the
        plan's dedicated identity query fills the gaps. A partial identity with a
        blank storey_name would resolve every element to the `unassigned` scene
        even though a real storey scene exists in the manifest, so completion is
        what keeps the viewer on real geometry.

        Never raises: the answer is the product, the highlight is a presentation
        of it, so a failure here must not fail the question.
        """
        try:
            elements = harvest_elements(records)
            if not (plan and plan.visualization_cypher):
                return elements
            incomplete = elements and any(
                not element.get("storey_name") for element in elements
            )
            if not elements or incomplete:
                is_valid, warnings = client.validate(plan.visualization_cypher, parameters)
                if not is_valid:
                    logger.warning(
                        "Visualization query for intent=%s failed validation: %s",
                        plan.intent, warnings,
                    )
                    return elements
                resolved = harvest_elements(client.run(plan.visualization_cypher, parameters))
                if not elements:
                    return resolved
                # Order and identity come from the answer's own rows; the plan's
                # query only fills what they lack (typically storey_name).
                return merge_element_records(elements, resolved)
            return elements
        except Exception as exc:
            logger.warning("Element resolution for visualization failed: %s", exc)
            return elements

    def _empty_result_message(self, cypher: str, client: Neo4jClient) -> str:
        """Distinguish 'genuinely no data' from 'filter is probably wrong'
        when a query returns zero rows, instead of returning the same
        generic message either way. If nodes of the queried label DO
        exist elsewhere in the graph, that's a strong signal the query's
        WHERE clause - not the underlying data - is the problem."""
        label_match = _LABEL_IN_QUERY.search(cypher)
        if not label_match:
            return "No matching elements found in the building graph."

        label = label_match.group(1)
        try:
            total = client.count_label(label)
        except Exception as exc:
            logger.warning("Could not sanity-check label count for %s: %s", label, exc)
            return "No matching elements found in the building graph."

        if total > 0:
            return (
                f"No matching elements found. Note: {total} {label} node(s) "
                f"exist in the building graph, but none matched this query's "
                f"filter conditions — the filter may be incorrect rather "
                f"than the data being absent. Consider rephrasing with a "
                f"different identifier (e.g. name instead of ID, or vice versa)."
            )
        return f"No {label} nodes exist in the building graph at all."

    def _format_records(
        self, records: List[Dict], cypher: str, client: Neo4jClient
    ) -> tuple:
        """Convert Neo4j records to human-readable context + source metadata."""
        if not records:
            return self._empty_result_message(cypher, client), []

        lines = []

        for i, record in enumerate(records[:25], 1):  # cap at 25 for token limits
            line_parts = []

            for key, value in record.items():
                # Neo4jClient.run() calls Record.data(), which turns driver Node
                # objects into plain property dicts and drops their labels. So a
                # returned node arrives here as a dict, not as an object with
                # .labels - hence the structural check. Rendering it as a raw
                # dict would truncate at the 100-char scalar cap below and hide
                # the very IDs the answer needs to cite.
                if looks_like_element_node(value):
                    name = value.get("name") or "Unnamed"
                    elem_id = value.get("id") or "N/A"
                    ifc_guid = value.get("ifcGuid") or elem_id
                    tag = value.get("tag")
                    ifc_type = value.get("ifcType") or "Element"
                    id_display = f"id={elem_id}, ifcGuid={ifc_guid}" + (f", tag={tag}" if tag else "")
                    line_parts.append(f"{key}={ifc_type}({id_display}, name={name})")

                elif isinstance(value, tuple) and len(value) == 3:
                    # Record.data() renders a relationship as
                    # (start_properties, type_name, end_properties).
                    start, rel_type, end = value
                    start_name = start.get("name", "N/A") if isinstance(start, dict) else "?"
                    end_name = end.get("name", "N/A") if isinstance(end, dict) else "?"
                    line_parts.append(f"{key}=[:{rel_type}]({start_name}\u2192{end_name})")

                elif isinstance(value, list):
                    line_parts.append(f"{key}=[{len(value)} items]")

                else:
                    # Scalar
                    str_val = str(value) if value is not None else "null"
                    if len(str_val) > 100:
                        str_val = str_val[:100] + "..."
                    line_parts.append(f"{key}={str_val}")

            lines.append(f"  {i}. " + " | ".join(line_parts))

        # Citation chips for the elements behind the displayed rows. Derived from
        # the same harvest the 3D viewer uses, so the chips and the highlighted
        # geometry can never disagree about which elements the answer cited.
        sources = [
            {
                "type": "graph",
                "element_id": element["element_id"],
                "ifc_guid": element["ifc_guid"],
                "name": element["name"],
                "ifc_type": element["ifc_type"],
                "storey_name": element["storey_name"],
            }
            for element in harvest_elements(records[:25])
        ]

        header = (
            f"Building Graph Results ({len(records)} total, "
            f"showing top {min(len(records), 25)}):\n"
        )
        context = header + "\n".join(lines)
        return context, sources
