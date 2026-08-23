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
            "record_count": len(records),
        }

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
        sources = []

        for i, record in enumerate(records[:25], 1):  # cap at 25 for token limits
            line_parts = []

            for key, value in record.items():
                if hasattr(value, "labels"):  # Neo4j Node object
                    props = dict(value)
                    label = list(value.labels)[0] if value.labels else "Element"
                    name = props.get("name", "Unnamed")
                    elem_id = props.get("id", "N/A")
                    ifc_guid = props.get("ifcGuid", elem_id)
                    tag = props.get("tag")
                    ifc_type = props.get("ifcType", label)
                    id_display = f"id={elem_id}, ifcGuid={ifc_guid}" + (f", tag={tag}" if tag else "")
                    line_parts.append(
                        f"{key}={ifc_type}({id_display}, name={name})"
                    )
                    sources.append({
                        "type": "graph",
                        "element_id": elem_id,
                        "ifc_guid": ifc_guid,
                        "tag": tag,
                        "name": name,
                        "ifc_type": ifc_type,
                        "source_ifc_file": props.get("sourceIfcFile"),
                        "project_id": props.get("projectId"),
                    })

                elif hasattr(value, "type"):  # Neo4j Relationship object
                    rel_type = value.type
                    start = (
                        value.start_node.get("name", "N/A")
                        if hasattr(value, "start_node") else "?"
                    )
                    end = (
                        value.end_node.get("name", "N/A")
                        if hasattr(value, "end_node") else "?"
                    )
                    line_parts.append(f"{key}=[:{rel_type}]({start}\u2192{end})")

                elif isinstance(value, list):
                    line_parts.append(f"{key}=[{len(value)} items]")

                else:
                    # Scalar
                    str_val = str(value) if value is not None else "null"
                    if len(str_val) > 100:
                        str_val = str_val[:100] + "..."
                    line_parts.append(f"{key}={str_val}")

            lines.append(f"  {i}. " + " | ".join(line_parts))

            # Planned relationship listings return scalar projections rather
            # than Node objects. Preserve their endpoint IDs as UI sources.
            for prefix in ("element_a", "element_b"):
                elem_id = record.get(f"{prefix}_id")
                if elem_id:
                    sources.append({
                        "type": "graph", "element_id": elem_id,
                        "name": record.get(f"{prefix}_name") or "",
                    })

        header = (
            f"Building Graph Results ({len(records)} total, "
            f"showing top {min(len(records), 25)}):\n"
        )
        context = header + "\n".join(lines)
        return context, sources
