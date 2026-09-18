"""Project/file-scoped graph cleanup for explicit IFC model deletion."""

from __future__ import annotations

from typing import Any


def delete_ifc_file_scope(client: Any, project_id: str, file_ids: list[str]) -> dict[str, int]:
    """Delete only graph/derived data owned by selected IFC files.

    Element ids are composite, so each selected element belongs to exactly one
    registered model. ``DETACH DELETE`` removes every relationship touching
    those elements, including cross-file clashes. Sustainability runs spanning
    a deleted file are invalid as a whole and are removed before their models.
    """
    file_ids = sorted(set(file_ids))
    params = {"project_id": project_id, "file_ids": file_ids}
    if not file_ids:
        return {
            "elements_deleted": 0,
            "models_deleted": 0,
            "issues_deleted": 0,
            "sustainability_runs_deleted": 0,
        }

    issues = client.run(
        "MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element) "
        "WHERE (a.projectId = $project_id AND a.sourceFileId IN $file_ids) "
        "OR (b.projectId = $project_id AND b.sourceFileId IN $file_ids) "
        "RETURN count(r) AS n",
        params,
    )
    elements = client.run(
        "MATCH (e:Element) WHERE e.projectId = $project_id "
        "AND e.sourceFileId IN $file_ids RETURN count(e) AS n",
        params,
    )
    models = client.run(
        "MATCH (m:IFCModel) WHERE m.projectId = $project_id "
        "AND m.sourceFileId IN $file_ids RETURN count(m) AS n",
        params,
    )
    runs = client.run(
        "MATCH (run:SustainabilityRun) WHERE run.projectId = $project_id "
        "AND any(fileId IN coalesce(run.selectedFileIds, []) WHERE fileId IN $file_ids) "
        "RETURN count(run) AS n",
        params,
    )

    # A run that referenced a removed model is no longer a valid deterministic
    # result. Its result nodes are run-owned, so sibling scopes remain intact.
    client.run(
        "MATCH (run:SustainabilityRun)-[:HAS_RESULT]->(result:SustainabilityResult) "
        "WHERE run.projectId = $project_id "
        "AND any(fileId IN coalesce(run.selectedFileIds, []) WHERE fileId IN $file_ids) "
        "DETACH DELETE result",
        params,
    )
    client.run(
        "MATCH (run:SustainabilityRun) WHERE run.projectId = $project_id "
        "AND any(fileId IN coalesce(run.selectedFileIds, []) WHERE fileId IN $file_ids) "
        "DETACH DELETE run",
        params,
    )
    for label in ("QuantityEvidence", "MaterialUse"):
        client.run(
            f"MATCH (n:{label}) WHERE coalesce(n.projectId, n.project_id) = $project_id "
            "AND coalesce(n.sourceFileId, n.source_file_id) IN $file_ids DETACH DELETE n",
            params,
        )

    # Deleting selected Elements removes HAS_ELEMENT, containment, material,
    # and every same/cross-file CLASHES_WITH relationship that touches them.
    client.run(
        "MATCH (e:Element) WHERE e.projectId = $project_id "
        "AND e.sourceFileId IN $file_ids DETACH DELETE e",
        params,
    )
    client.run(
        "MATCH (m:IFCModel) WHERE m.projectId = $project_id "
        "AND m.sourceFileId IN $file_ids DETACH DELETE m",
        params,
    )
    client.run(
        "MATCH (p:BIMProject {id: $project_id}) "
        "WHERE NOT (p)-[:HAS_MODEL]->(:IFCModel) DETACH DELETE p",
        params,
    )
    def count(rows: list[dict[str, Any]]) -> int:
        return int(rows[0].get("n", 0)) if rows else 0

    return {
        "elements_deleted": count(elements),
        "models_deleted": count(models),
        "issues_deleted": count(issues),
        "sustainability_runs_deleted": count(runs),
    }
