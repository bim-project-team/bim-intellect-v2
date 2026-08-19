"""
load_to_neo4j.py
------------------
Loads nodes.csv / edges.csv (from extract_graph.py) into Neo4j using the
Python driver with batched UNWIND queries, instead of Cypher's LOAD CSV.

Why UNWIND from the driver instead of LOAD CSV:
LOAD CSV can only read files already inside Neo4j's import/ directory,
which means manually copying files into the Docker container every run.
Batched UNWIND sends the data straight from Python over the Bolt
connection, so this script is a self-contained pipeline step: point it at
your CSVs and it just works, no container filesystem juggling.

Run:
    python -m bim_graph.load_to_neo4j
"""

import math
import pandas as pd

from bim_graph.config import NODES_CSV, EDGES_CSV, BATCH_SIZE
from bim_graph.neo4j_client import Neo4jClient

# Cypher for one batch of node rows. Uses apoc.create.addLabels so the IFC
# type (only known at load time, from the CSV) becomes both a property AND
# a queryable label, e.g. MATCH (w:IfcWall).
NODE_QUERY = """
UNWIND $rows AS row
MERGE (e:Element {id: row.id})
SET e.ifcType     = row.type,
    e.name         = row.name,
    e.storeyId     = row.storey_id,
    e.storeyName   = row.storey_name,
    e.minX = row.min_x, e.minY = row.min_y, e.minZ = row.min_z,
    e.maxX = row.max_x, e.maxY = row.max_y, e.maxZ = row.max_z
WITH e, row.type AS ifcType
CALL apoc.create.addLabels(e, [ifcType]) YIELD node
RETURN count(*)
"""

# Cypher for one batch of edge rows. apoc.create.relationship lets the
# relationship type be data-driven (AGGREGATES / CONTAINS / BOUNDS /
# PORT_OF) instead of one MERGE per fixed type.
EDGE_QUERY = """
UNWIND $rows AS row
MATCH (a:Element {id: row.source_id})
MATCH (b:Element {id: row.target_id})
CALL apoc.merge.relationship(a, row.rel_type, {}, {}, b, {}) YIELD rel
RETURN count(*)
"""

CONSTRAINT_QUERY = """
CREATE CONSTRAINT element_id IF NOT EXISTS
FOR (e:Element) REQUIRE e.id IS UNIQUE
"""


def _clean_value(v):
    """pandas gives NaN for empty CSV cells; Neo4j has no concept of NaN,
    so convert to None (stored as a missing property, not a NaN value)."""
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def load_rows(path):
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[load] {path} is not valid UTF-8, retrying as cp1252...", flush=True)
        df = pd.read_csv(path, encoding="cp1252")
    records = df.to_dict("records")
    return [{k: _clean_value(v) for k, v in row.items()} for row in records]


def load(nodes_csv=NODES_CSV, edges_csv=EDGES_CSV, reset=False):
    """
    Public entrypoint: load nodes_csv/edges_csv into Neo4j. Returns a
    summary dict so a caller (e.g. the FastAPI /ingest endpoint) can report
    results without parsing stdout.
    """
    print(f"[load] connecting to Neo4j...", flush=True)
    with Neo4jClient() as client:
        client.verify_connectivity()
        print(f"[load] connected.", flush=True)

        if reset:
            print(f"[load] reset=True — deleting all existing nodes/relationships...", flush=True)
            client.run("MATCH (n) DETACH DELETE n")
            print(f"[load] graph cleared.", flush=True)

        client.run(CONSTRAINT_QUERY)

        print(f"[load] reading {nodes_csv}...", flush=True)
        nodes = load_rows(nodes_csv)
        print(f"[load] writing {len(nodes)} node(s) in batches of {BATCH_SIZE}...", flush=True)
        client.run_batched(NODE_QUERY, nodes, BATCH_SIZE)
        print(f"[load] nodes written.", flush=True)

        print(f"[load] reading {edges_csv}...", flush=True)
        edges = load_rows(edges_csv)
        print(f"[load] writing {len(edges)} edge(s) in batches of {BATCH_SIZE}...", flush=True)
        client.run_batched(EDGE_QUERY, edges, BATCH_SIZE)
        rel_count_after = client.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        print(f"[load] edges written — {rel_count_after} relationship(s) now in graph.", flush=True)

        node_counts = client.run(
            "MATCH (e:Element) RETURN e.ifcType AS type, count(*) AS n ORDER BY n DESC"
        )
        rel_counts = client.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC"
        )

        edges_skipped = max(0, len(edges) - rel_count_after)
        if edges_skipped:
            print(f"[load] WARNING: {edges_skipped} edge row(s) were silently skipped by MATCH "
                  f"(dangling source/target id) — run `python -m bim_graph.diagnose_load` to see which.",
                  flush=True)

        print(f"[load] done. {len(nodes)} nodes attempted, {rel_count_after} relationships in graph.",
              flush=True)

        return {
            "reset": reset,
            "nodes_attempted": len(nodes),
            "edges_attempted": len(edges),
            "relationships_in_graph": rel_count_after,
            "edges_skipped": edges_skipped,
            "node_counts_by_type": {r["type"]: r["n"] for r in node_counts},
            "relationship_counts_by_type": {r["type"]: r["n"] for r in rel_counts},
        }


def main():
    import sys
    summary = load(reset="--reset" in sys.argv)

    print("\nNode counts by type:")
    for t, n in summary["node_counts_by_type"].items():
        print(f"  {t:30s} {n}")

    print("\nRelationship counts by type:")
    for t, n in summary["relationship_counts_by_type"].items():
        print(f"  {t:15s} {n}")


if __name__ == "__main__":
    main()