"""
diagnose_load.py
------------------
Compares nodes.csv / edges.csv against what actually landed in Neo4j after
load_to_neo4j.py, to find rows that silently failed to load. Cypher's
MATCH clause doesn't error when it finds nothing - a relationship row
whose source or target id isn't a node in the graph just quietly produces
zero rows, so missing data here won't show up as an error anywhere else.

Run:
    python -m bim_graph.diagnose_load
"""

import pandas as pd

from bim_graph.config import NODES_CSV, EDGES_CSV
from bim_graph.neo4j_client import Neo4jClient


def diagnose_edges(edges_df, client):
    csv_by_type = edges_df["rel_type"].value_counts().to_dict()
    # De-duplicated counts: apoc.merge.relationship collapses rows that are
    # identical in (source_id, target_id, rel_type), so a CSV with true
    # duplicate rows will legitimately produce fewer graph relationships
    # than raw row count without anything being "missing".
    dedup_by_type = (
        edges_df.drop_duplicates(subset=["source_id", "target_id", "rel_type"])["rel_type"]
        .value_counts().to_dict()
    )
    graph_rows = client.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n")
    graph_by_type = {row["type"]: row["n"] for row in graph_rows}

    print("=== Edge rel_type: CSV rows (raw / de-duplicated) vs. graph relationships ===")
    for rel_type in sorted(set(csv_by_type) | set(graph_by_type)):
        csv_n = csv_by_type.get(rel_type, 0)
        dedup_n = dedup_by_type.get(rel_type, 0)
        graph_n = graph_by_type.get(rel_type, 0)
        flag = "  <-- STILL MISSING after accounting for duplicates" if graph_n < dedup_n else ""
        print(f"  {rel_type:15s} raw={csv_n:5d}  dedup={dedup_n:5d}  graph={graph_n:5d}{flag}")

    # For any rel_type with a gap, find which endpoint ids never made it in
    # as nodes - that's almost always the root cause of a silent MATCH miss.
    graph_ids = {r["id"] for r in client.run("MATCH (e:Element) RETURN e.id AS id")}
    for rel_type, dedup_n in dedup_by_type.items():
        if graph_by_type.get(rel_type, 0) < dedup_n:
            subset = edges_df[edges_df.rel_type == rel_type]
            missing_sources = set(subset.source_id) - graph_ids
            missing_targets = set(subset.target_id) - graph_ids
            print(f"\n  {rel_type}: {len(missing_sources)} source id(s) and "
                  f"{len(missing_targets)} target id(s) not found as nodes.")
            if missing_sources:
                print(f"    example missing source id: {next(iter(missing_sources))}")
            if missing_targets:
                print(f"    example missing target id: {next(iter(missing_targets))}")


def diagnose_nodes(nodes_df, client):
    csv_with_bbox = nodes_df.dropna(subset=["min_x", "min_y", "min_z", "max_x", "max_y", "max_z"])
    graph_with_bbox = client.run(
        "MATCH (e:Element) WHERE e.minX IS NOT NULL RETURN e.id AS id, e.ifcType AS type"
    )
    graph_bbox_ids = {r["id"] for r in graph_with_bbox}
    csv_bbox_ids = set(csv_with_bbox["id"])

    print(f"\n=== Nodes with a bounding box: CSV={len(csv_bbox_ids)}  graph={len(graph_bbox_ids)} ===")
    missing = csv_bbox_ids - graph_bbox_ids
    if missing:
        missing_rows = nodes_df[nodes_df.id.isin(missing)]
        print(f"  {len(missing)} ids had a bbox in the CSV but not in the graph.")
        print("  breakdown by type:")
        print(missing_rows["type"].value_counts().to_string())
    else:
        print("  All CSV bounding boxes made it into the graph.")


if __name__ == "__main__":
    nodes_df = pd.read_csv(NODES_CSV)
    edges_df = pd.read_csv(EDGES_CSV)

    with Neo4jClient() as client:
        diagnose_edges(edges_df, client)
        diagnose_nodes(nodes_df, client)