"""
clash_pipeline.py
------------------
Closes the loop: reads elements back OUT of Neo4j (not the CSV), runs the
same AABB clash/clearance logic as detect_clashes.py, and writes results
back IN as :CLASHES_WITH relationships. After this runs, Neo4j holds both
the building structure and the detected issues, queryable together.

Run:
    python -m bim_graph.clash_pipeline
"""

import pandas as pd
from itertools import combinations

from bim_graph.neo4j_client import Neo4jClient

CLEARANCE_THRESHOLD = 0.25  # feet, ~3 inches - see detect_clashes.py for rationale

IGNORE_TYPE_PAIRS = {
    frozenset({"IfcWallStandardCase", "IfcWallStandardCase"}),
    frozenset({"IfcSpace", "IfcSpace"}),
    frozenset({"IfcRailing", "IfcWallStandardCase"}),
    frozenset({"IfcRailing", "IfcStair"}),
    frozenset({"IfcRailing", "IfcSlab"}),
    frozenset({"IfcRailing", "IfcRailing"}),
    frozenset({"IfcDoor", "IfcWallStandardCase"}),
    frozenset({"IfcDoor", "IfcSlab"}),
    frozenset({"IfcDoor", "IfcDoor"}),
    frozenset({"IfcCovering", "IfcWallStandardCase"}),
    frozenset({"IfcCovering", "IfcCovering"}),
    frozenset({"IfcSlab", "IfcWallStandardCase"}),
    frozenset({"IfcStair", "IfcWallStandardCase"}),
    frozenset({"IfcSlab", "IfcStair"}),
}

FETCH_QUERY = """
MATCH (e:Element)
WHERE e.minX IS NOT NULL
RETURN e.id AS id, e.ifcType AS type, e.name AS name,
       e.storeyName AS storey_name,
       e.minX AS min_x, e.minY AS min_y, e.minZ AS min_z,
       e.maxX AS max_x, e.maxY AS max_y, e.maxZ AS max_z
"""

WRITE_CLASH_QUERY = """
UNWIND $rows AS row
MATCH (a:Element {id: row.a_id})
MATCH (b:Element {id: row.b_id})
MERGE (a)-[r:CLASHES_WITH]->(b)
SET r.issue = row.issue, r.metric = row.metric
"""


def aabb_gap(a, b):
    gaps = []
    for lo_key, hi_key in (("min_x", "max_x"), ("min_y", "max_y"), ("min_z", "max_z")):
        gap = max(a[lo_key], b[lo_key]) - min(a[hi_key], b[hi_key])
        gaps.append(gap)
    return gaps


def classify_pair(a, b):
    gaps = aabb_gap(a, b)
    if all(g < 0 for g in gaps):
        volume = 1
        for g in gaps:
            volume *= -g
        return "CLASH", volume

    positive_gaps = [max(g, 0) for g in gaps]
    distance = sum(g ** 2 for g in positive_gaps) ** 0.5
    if distance < CLEARANCE_THRESHOLD:
        return "CLEARANCE_VIOLATION", distance
    return None, distance


def detect(elements_df):
    results = []
    for storey_name, group in elements_df.groupby("storey_name"):
        elements = group.to_dict("records")
        for a, b in combinations(elements, 2):
            if frozenset({a["type"], b["type"]}) in IGNORE_TYPE_PAIRS:
                continue
            label, value = classify_pair(a, b)
            if label is None:
                continue
            results.append({"a_id": a["id"], "b_id": b["id"], "issue": label, "metric": round(value, 4)})
    return results


LIST_ISSUES_QUERY = """
MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
WHERE $issue IS NULL OR r.issue = $issue
RETURN a.id AS a_id, a.name AS a_name, a.ifcType AS a_type,
       b.id AS b_id, b.name AS b_name, b.ifcType AS b_type,
       r.issue AS issue, r.metric AS metric
ORDER BY r.metric DESC
"""


def list_issues(issue=None):
    """Return detected CLASHES_WITH rows, optionally filtered to one issue
    type ('CLASH' or 'CLEARANCE_VIOLATION'). Used by the API's read
    endpoints so they don't need their own Cypher."""
    with Neo4jClient() as client:
        return client.run(LIST_ISSUES_QUERY, {"issue": issue})


def run_clash_detection():
    """
    Public entrypoint: fetch elements from Neo4j, run AABB clash/clearance
    detection, write CLASHES_WITH relationships back in. Returns a summary
    dict so a caller (e.g. the FastAPI /analyze endpoint) can report
    results without parsing stdout.
    """
    print(f"[clash] connecting to Neo4j...", flush=True)
    with Neo4jClient() as client:
        client.verify_connectivity()
        print(f"[clash] connected.", flush=True)

        print(f"[clash] fetching elements with bounding boxes from the graph...", flush=True)
        rows = client.run(FETCH_QUERY)
        df = pd.DataFrame(rows)
        print(f"[clash] fetched {len(df)} element(s).", flush=True)

        if len(df) == 0:
            print(f"[clash] WARNING: no elements with bounding boxes found — did the "
                  f"ingestion step run first?", flush=True)

        storey_count = df["storey_name"].nunique() if len(df) else 0
        print(f"[clash] running AABB clash/clearance checks across {storey_count} storey(s)...",
              flush=True)
        clashes = detect(df) if len(df) else []
        print(f"[clash] detected {len(clashes)} issue(s).", flush=True)

        if clashes:
            print(f"[clash] writing CLASHES_WITH relationship(s) back to the graph...", flush=True)
            client.run_batched(WRITE_CLASH_QUERY, clashes, batch_size=1000)
            print(f"[clash] written.", flush=True)

        summary_rows = client.run(
            "MATCH ()-[r:CLASHES_WITH]->() RETURN r.issue AS issue, count(*) AS n ORDER BY n DESC"
        )

        print(f"[clash] done. {len(df)} elements checked, {len(clashes)} issue(s) detected.",
              flush=True)

        return {
            "elements_checked": len(df),
            "issues_detected": len(clashes),
            "issues_by_type": {r["issue"]: r["n"] for r in summary_rows},
        }


def main():
    summary = run_clash_detection()
    print("\nCLASHES_WITH summary in graph:")
    for issue, n in summary["issues_by_type"].items():
        print(f"  {issue:20s} {n}")


if __name__ == "__main__":
    main()