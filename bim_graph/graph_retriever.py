"""Retrieves and formats graph data from Neo4j for RAG context.

End-to-end flow: natural language → Cypher → Neo4j execution → human-readable
context string + structured source metadata.
"""

import logging
from typing import Dict, Any, List

from bim_graph.neo4j_client import Neo4jClient
from bim_graph.cypher_generator import CypherGenerator

logger = logging.getLogger("bim_intellect.graph_retriever")


class GraphRetriever:
    """Graph RAG: question → Cypher → execution → formatted LLM context."""

    def __init__(self):
        self.cypher_gen = CypherGenerator()

    def ask(self, question: str) -> Dict[str, Any]:
        """Generate Cypher, run it, and format results for LLM consumption."""
        cypher = self.cypher_gen.generate(question)

        try:
            with Neo4jClient() as client:
                records = client.run(cypher)
        except Exception as exc:
            logger.error("Cypher execution failed: %s | Query: %s", exc, cypher[:200])
            raise RuntimeError(f"Graph query execution failed: {exc}") from exc

        context, sources = self._format_records(records)

        return {
            "cypher_query": cypher,
            "context": context,
            "sources": sources,
            "record_count": len(records),
        }

    def _format_records(self, records: List[Dict]) -> tuple:
        """Convert Neo4j records to human-readable context + source metadata."""
        if not records:
            return "No matching elements found in the building graph.", []

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
                    ifc_type = props.get("ifcType", label)
                    line_parts.append(
                        f"{key}={ifc_type}(id={elem_id}, name={name})"
                    )
                    sources.append({
                        "type": "graph",
                        "element_id": elem_id,
                        "name": name,
                        "ifc_type": ifc_type,
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
                    line_parts.append(f"{key}=[:{rel_type}]({start}→{end})")

                elif isinstance(value, list):
                    line_parts.append(f"{key}=[{len(value)} items]")

                else:
                    # Scalar
                    str_val = str(value) if value is not None else "null"
                    if len(str_val) > 100:
                        str_val = str_val[:100] + "..."
                    line_parts.append(f"{key}={str_val}")

            lines.append(f"  {i}. " + " | ".join(line_parts))

        header = (
            f"Building Graph Results ({len(records)} total, "
            f"showing top {min(len(records), 25)}):\n"
        )
        context = header + "\n".join(lines)
        return context, sources
