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

import warnings

import pandas as pd

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
  AND ($project_id IS NULL OR e.projectId = $project_id)
  AND (size($file_ids) = 0 OR e.sourceFileId IN $file_ids)
RETURN e.id AS id, e.ifcType AS type, e.name AS name,
       coalesce(e.ifcGuid, e.id) AS ifc_guid,
       e.sourceIfcFile AS source_ifc_file, e.sourceFileId AS source_file_id,
       e.discipline AS discipline, e.projectId AS project_id,
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
    , r.projectId = row.project_id
    , r.sourceIfcFileA = row.a_source_ifc_file
    , r.sourceIfcFileB = row.b_source_ifc_file
    , r.ifcGuidA = row.a_ifc_guid
    , r.ifcGuidB = row.b_ifc_guid
    , r.crossFile = row.cross_file
"""

CLEAR_PROJECT_ISSUES_QUERY = """
MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
WHERE r.projectId = $project_id
  AND (size($file_ids) = 0 OR (a.sourceFileId IN $file_ids AND b.sourceFileId IN $file_ids))
DELETE r
"""

# Anomaly inference reads the same graph schema as the rule engine, but it
# explicitly excludes CLASHES_WITH so rule outputs never leak into ML inputs.
ANOMALY_NODE_FETCH_QUERY = """
MATCH (e:Element)
RETURN e.id AS id, e.ifcType AS ifcType, e.name AS name,
       e.storeyId AS storeyId, e.storeyName AS storeyName,
       e.minX AS minX, e.minY AS minY, e.minZ AS minZ,
       e.maxX AS maxX, e.maxY AS maxY, e.maxZ AS maxZ
"""

ANOMALY_EDGE_FETCH_QUERY = """
MATCH (a:Element)-[r]->(b:Element)
WHERE type(r) IN ['AGGREGATES', 'CONTAINS', 'BOUNDS', 'PORT_OF']
RETURN a.id AS source_id, b.id AS target_id, type(r) AS rel_type
"""

WRITE_ELEMENT_ANOMALY_QUERY = """
UNWIND $rows AS row
MATCH (e:Element {id: row.id})
SET e.anomalyScore = row.anomaly_score,
    e.anomalyFeatureError = row.feature_error,
    e.anomalyStructuralError = row.structural_error,
    e.isAnomaly = row.is_anomaly
"""

WRITE_CLASH_ANOMALY_QUERY = """
UNWIND $rows AS row
MATCH (a:Element {id: row.a_id})-[r:CLASHES_WITH]->(b:Element {id: row.b_id})
SET r.anomalyScoreA = row.anomaly_score_a,
    r.anomalyScoreB = row.anomaly_score_b,
    r.combinedAnomalyScore = row.combined_anomaly_score
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
    """Sweep-and-prune in shared world coordinates, including cross-file pairs."""
    results = []
    elements = sorted(elements_df.to_dict("records"), key=lambda row: row["min_x"])
    active = []
    for b in elements:
        active = [a for a in active if a["max_x"] + CLEARANCE_THRESHOLD >= b["min_x"]]
        for a in active:
            # A GlobalId identifies the same IFC object across discipline exports.
            # Comparing duplicate exports of that object would manufacture a clash.
            if (a.get("ifc_guid") and a.get("ifc_guid") == b.get("ifc_guid")
                    and a.get("source_file_id") != b.get("source_file_id")):
                continue
            if frozenset({a["type"], b["type"]}) in IGNORE_TYPE_PAIRS:
                continue
            label, value = classify_pair(a, b)
            if label is None:
                continue
            results.append({
                "a_id": a["id"], "b_id": b["id"], "issue": label,
                "metric": round(value, 4),
                "a_ifc_guid": a.get("ifc_guid", a["id"]),
                "b_ifc_guid": b.get("ifc_guid", b["id"]),
                "a_source_ifc_file": a.get("source_ifc_file"),
                "b_source_ifc_file": b.get("source_ifc_file"),
                "a_discipline": a.get("discipline"),
                "b_discipline": b.get("discipline"),
                "project_id": a.get("project_id") or b.get("project_id"),
                "cross_file": bool(
                    a.get("source_file_id") and b.get("source_file_id")
                    and a.get("source_file_id") != b.get("source_file_id")
                ),
            })
        active.append(b)
    return results


LIST_ISSUES_QUERY = """
MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
WHERE $issue IS NULL OR r.issue = $issue
RETURN a.id AS a_id, a.name AS a_name, a.ifcType AS a_type,
       coalesce(a.ifcGuid, a.id) AS a_guid, a.sourceIfcFile AS a_source_ifc_file,
       b.id AS b_id, b.name AS b_name, b.ifcType AS b_type,
       coalesce(b.ifcGuid, b.id) AS b_guid, b.sourceIfcFile AS b_source_ifc_file,
       r.issue AS issue, r.metric AS metric,
       r.projectId AS project_id, r.crossFile AS cross_file,
       r[$anomaly_score_a_property] AS anomaly_score_a,
       r[$anomaly_score_b_property] AS anomaly_score_b,
       r[$combined_anomaly_score_property] AS combined_anomaly_score
ORDER BY r.metric DESC
"""


def list_issues(issue=None):
    """Return detected CLASHES_WITH rows, optionally filtered to one issue
    type ('CLASH' or 'CLEARANCE_VIOLATION'). Used by the API's read
    endpoints so they don't need their own Cypher."""
    with Neo4jClient() as client:
        return client.run(LIST_ISSUES_QUERY, {
            "issue": issue,
            "anomaly_score_a_property": "anomalyScoreA",
            "anomaly_score_b_property": "anomalyScoreB",
            "combined_anomaly_score_property": "combinedAnomalyScore",
        })


def run_clash_detection(
    *,
    anomaly_model_enabled=False,
    anomaly_checkpoint=None,
    anomaly_detector=None,
    anomaly_combination="max",
    anomaly_device="auto",
    project_id=None,
    file_ids=None,
):
    """
    Public entrypoint: fetch elements from Neo4j, run AABB clash/clearance
    detection, write CLASHES_WITH relationships back in. Returns a summary
    dict so a caller (e.g. the FastAPI /analyze endpoint) can report
    results without parsing stdout.
    """
    detector = anomaly_detector
    anomaly_status = "disabled"
    if anomaly_model_enabled and detector is None:
        try:
            from bim_graph.anomaly.inference import GraphAnomalyDetector
            detector = GraphAnomalyDetector(
                anomaly_checkpoint or "artifacts/anomaly/graph_autoencoder.pt",
                device=anomaly_device,
            )
            anomaly_status = "ready"
        except Exception as exc:
            warnings.warn(
                f"Anomaly inference is unavailable ({exc}); continuing with authoritative rule checks.",
                RuntimeWarning,
                stacklevel=2,
            )
            detector = None
            anomaly_status = "unavailable"
    elif anomaly_model_enabled:
        anomaly_status = "ready"

    print(f"[clash] connecting to Neo4j...", flush=True)
    with Neo4jClient() as client:
        client.verify_connectivity()
        print(f"[clash] connected.", flush=True)

        print(f"[clash] fetching elements with bounding boxes from the graph...", flush=True)
        file_ids = list(file_ids or [])
        query_params = {"project_id": project_id, "file_ids": file_ids}
        rows = client.run(FETCH_QUERY, query_params)
        df = pd.DataFrame(rows)
        print(f"[clash] fetched {len(df)} element(s).", flush=True)

        if len(df) == 0:
            print(f"[clash] WARNING: no elements with bounding boxes found — did the "
                  f"ingestion step run first?", flush=True)

        storey_count = df["storey_name"].nunique() if len(df) and "storey_name" in df else 0
        print(f"[clash] running AABB clash/clearance checks across {storey_count} storey label(s)...",
              flush=True)
        clashes = detect(df) if len(df) else []
        print(f"[clash] detected {len(clashes)} issue(s).", flush=True)

        anomaly_scores = None
        if detector is not None:
            try:
                print("[clash] scoring the BIM graph with the optional anomaly model...", flush=True)
                anomaly_nodes = client.run(ANOMALY_NODE_FETCH_QUERY)
                anomaly_edges = client.run(ANOMALY_EDGE_FETCH_QUERY)
                anomaly_scores = detector.score_records(anomaly_nodes, anomaly_edges, source_file="<neo4j>")
                if len(anomaly_scores):
                    from bim_graph.anomaly.inference import enrich_clash_results
                    clashes = enrich_clash_results(clashes, anomaly_scores, anomaly_combination)
                    score_rows = [
                        {
                            "id": row.IFC_GUID,
                            "anomaly_score": float(row.Anomaly_Score),
                            "feature_error": float(row.Feature_Error),
                            "structural_error": float(row.Structural_Error),
                            "is_anomaly": bool(row.Is_Anomaly),
                        }
                        for row in anomaly_scores.itertuples(index=False)
                    ]
                    client.run_batched(WRITE_ELEMENT_ANOMALY_QUERY, score_rows, batch_size=1000)
                anomaly_status = "scored"
                print(f"[clash] scored {len(anomaly_scores)} graph element(s).", flush=True)
            except Exception as exc:
                warnings.warn(
                    f"Anomaly scoring failed ({exc}); continuing with rule-only results.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                anomaly_scores = None
                anomaly_status = "failed"

        # A scoped rerun replaces prior results even when the new run finds zero
        # issues; otherwise stale project clashes would remain visible.
        if project_id:
            client.run(CLEAR_PROJECT_ISSUES_QUERY, query_params)

        if clashes:
            print(f"[clash] writing CLASHES_WITH relationship(s) back to the graph...", flush=True)
            client.run_batched(WRITE_CLASH_QUERY, clashes, batch_size=1000)
            if anomaly_scores is not None:
                client.run_batched(WRITE_CLASH_ANOMALY_QUERY, clashes, batch_size=1000)
            print(f"[clash] written.", flush=True)

        summary_rows = client.run(
            "MATCH ()-[r:CLASHES_WITH]->() "
            "WHERE $project_id IS NULL OR r.projectId = $project_id "
            "RETURN r.issue AS issue, count(*) AS n ORDER BY n DESC",
            {"project_id": project_id},
        )

        print(f"[clash] done. {len(df)} elements checked, {len(clashes)} issue(s) detected.",
              flush=True)

        summary = {
            "elements_checked": len(df),
            "issues_detected": len(clashes),
            "issues_by_type": {r["issue"]: r["n"] for r in summary_rows},
        }
        if anomaly_model_enabled:
            summary.update({
                "anomaly_status": anomaly_status,
                "elements_scored": len(anomaly_scores) if anomaly_scores is not None else 0,
            })
        if project_id:
            summary.update({
                "project_id": project_id,
                "selected_file_ids": file_ids,
                "cross_file_issues": sum(1 for row in clashes if row.get("cross_file")),
            })
        return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anomaly", action="store_true", help="Enable optional graph anomaly enrichment")
    parser.add_argument("--anomaly-checkpoint", default="artifacts/anomaly/graph_autoencoder.pt")
    parser.add_argument("--anomaly-device", default="auto")
    parser.add_argument("--anomaly-combination", choices=("max", "mean"), default="max")
    args = parser.parse_args()
    summary = run_clash_detection(
        anomaly_model_enabled=args.anomaly,
        anomaly_checkpoint=args.anomaly_checkpoint,
        anomaly_device=args.anomaly_device,
        anomaly_combination=args.anomaly_combination,
    )
    print("\nCLASHES_WITH summary in graph:")
    for issue, n in summary["issues_by_type"].items():
        print(f"  {issue:20s} {n}")


if __name__ == "__main__":
    main()
