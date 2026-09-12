"""Neo4j persistence for versioned sustainability evidence and results."""

from __future__ import annotations

from typing import Any

from bim_graph.neo4j_client import Neo4jClient

from .config import EXTRACTOR_VERSION, NEO4J_BATCH_SIZE, NORMALIZATION_VERSION, SPATIAL_IFC_TYPES
from .factors import factor_record_id
from .models import CarbonFactor, CarbonResult, ElementEvidence

CONSTRAINTS = (
    "CREATE CONSTRAINT material_id IF NOT EXISTS FOR (n:Material) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT material_use_id IF NOT EXISTS FOR (n:MaterialUse) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT quantity_evidence_id IF NOT EXISTS FOR (n:QuantityEvidence) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT carbon_factor_id IF NOT EXISTS FOR (n:CarbonFactor) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT sustainability_run_id IF NOT EXISTS FOR (n:SustainabilityRun) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT sustainability_result_id IF NOT EXISTS FOR (n:SustainabilityResult) REQUIRE n.id IS UNIQUE",
)

RUN_QUERY = """
MERGE (run:SustainabilityRun {id: $row.id})
SET run += $row
WITH run
MATCH (p:BIMProject {id: $row.projectId})
MERGE (p)-[:HAS_SUSTAINABILITY_RUN]->(run)
WITH run
UNWIND $row.selectedFileIds AS fileId
MATCH (m:IFCModel {projectId: $row.projectId, sourceFileId: fileId})
MERGE (run)-[:ANALYZED_MODEL]->(m)
RETURN run.id AS id
"""

FACTOR_QUERY = """
UNWIND $rows AS row
MERGE (factor:CarbonFactor {id: row.id})
SET factor += row
"""

RESULT_QUERY = """
UNWIND $rows AS row
MATCH (run:SustainabilityRun {id: row.run_id})
MATCH (element:Element {id: row.element_id})
MERGE (result:SustainabilityResult {id: row.id})
SET result += row
MERGE (run)-[:HAS_RESULT]->(result)
MERGE (result)-[:FOR_ELEMENT]->(element)
"""

MATERIAL_USE_QUERY = """
UNWIND $rows AS row
MATCH (element:Element {id: row.element_id})
MERGE (material:Material {id: row.material_id})
SET material.normalizedName = row.normalized_name,
    material.category = row.category,
    material.normalizationVersion = row.normalization_version
MERGE (use:MaterialUse {id: row.id})
SET use += row.properties
MERGE (element)-[:HAS_MATERIAL_USE]->(use)
MERGE (use)-[:OF_MATERIAL]->(material)
"""

QUANTITY_QUERY = """
UNWIND $rows AS row
MATCH (element:Element {id: row.element_id})
MERGE (quantity:QuantityEvidence {id: row.id})
SET quantity += row.properties
MERGE (element)-[:HAS_QUANTITY_EVIDENCE]->(quantity)
"""

RESULT_LINK_QUERY = """
UNWIND $rows AS row
MATCH (result:SustainabilityResult {id: row.id})
OPTIONAL MATCH (use:MaterialUse {id: row.material_use_id})
FOREACH (_ IN CASE WHEN use IS NULL THEN [] ELSE [1] END |
    MERGE (result)-[:FOR_MATERIAL_USE]->(use))
WITH result, row
OPTIONAL MATCH (factor:CarbonFactor {id: row.factor_record_id})
FOREACH (_ IN CASE WHEN factor IS NULL THEN [] ELSE [1] END |
    MERGE (result)-[:USES_FACTOR]->(factor))
WITH result, row
OPTIONAL MATCH (quantity:QuantityEvidence {id: row.quantity_evidence_id})
FOREACH (_ IN CASE WHEN quantity IS NULL THEN [] ELSE [1] END |
    MERGE (result)-[:USES_QUANTITY]->(quantity))
"""


class SustainabilityRepository:
    def __init__(self, client_factory=Neo4jClient):
        self.client_factory = client_factory

    def ensure_schema(self, client: Any) -> None:
        for query in CONSTRAINTS:
            client.run(query)

    def fetch_elements(self, project_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        with self.client_factory() as client:
            return client.run(
                "MATCH (e:Element) "
                "WHERE e.projectId = $project_id AND e.sourceFileId IN $file_ids "
                "AND NOT e.ifcType IN $spatial_types "
                "RETURN e.id AS element_id, e.ifcGuid AS ifc_guid, e.ifcType AS ifc_type, "
                "e.name AS name, e.storeyName AS storey_name, "
                "e.minX AS min_x, e.minY AS min_y, e.minZ AS min_z, "
                "e.maxX AS max_x, e.maxY AS max_y, e.maxZ AS max_z, "
                "e.projectId AS project_id, e.sourceFileId AS source_file_id, "
                "e.sourceIfcFile AS source_ifc_file, e.discipline AS discipline "
                "ORDER BY e.sourceFileId, e.id",
                {"project_id": project_id, "file_ids": file_ids, "spatial_types": sorted(SPATIAL_IFC_TYPES)},
            )

    def persist(
        self,
        run: dict[str, Any],
        elements: list[ElementEvidence],
        results: list[CarbonResult],
        factors: list[CarbonFactor],
    ) -> None:
        result_rows = [{
            **item.to_dict(),
            "projectId": item.project_id,
            "sourceFileId": item.source_file_id,
            "sourceIfcFile": item.source_ifc_file,
            "ifcGuid": item.ifc_guid,
            "elementId": item.element_id,
        } for item in results]
        material_rows = []
        quantity_rows = []
        for element in elements:
            for material in element.materials:
                material_rows.append({
                    "id": material.id,
                    "element_id": element.element_id,
                    "material_id": material.normalized_name or f"unnamed:{material.material_ifc_id}",
                    "normalized_name": material.normalized_name,
                    "category": material.category,
                    "normalization_version": NORMALIZATION_VERSION,
                    "properties": {
                        **material.to_dict(),
                        "projectId": element.project_id,
                        "sourceFileId": element.source_file_id,
                        "sourceIfcFile": element.source_ifc_file,
                        "discipline": element.discipline,
                        "ifcGuid": element.ifc_guid,
                        "elementId": element.element_id,
                        "extractorVersion": EXTRACTOR_VERSION,
                    },
                })
            for quantity in element.quantities:
                quantity_rows.append({
                    "id": quantity.id,
                    "element_id": element.element_id,
                    "properties": {
                        **quantity.to_dict(),
                        "projectId": element.project_id,
                        "sourceFileId": element.source_file_id,
                        "sourceIfcFile": element.source_ifc_file,
                        "discipline": element.discipline,
                        "ifcGuid": element.ifc_guid,
                        "elementId": element.element_id,
                        "extractorVersion": EXTRACTOR_VERSION,
                    },
                })
        used_factor_ids = {item.factor_id for item in results if item.factor_id}
        factor_rows = [{
            **item.to_dict(),
            "id": factor_record_id(run["factorDatasetHash"], item.factor_id),
            "dataset_hash": run["factorDatasetHash"],
        } for item in factors if item.factor_id in used_factor_ids]
        run_row = {
            **run,
            "projectId": run["project_id"],
            "selectedFileIds": run["file_ids"],
            "isCurrent": False,
        }
        with self.client_factory() as client:
            self.ensure_schema(client)
            client.run(RUN_QUERY, {"row": run_row})
            if factor_rows:
                client.run_batched(FACTOR_QUERY, factor_rows, NEO4J_BATCH_SIZE)
            if material_rows:
                client.run_batched(MATERIAL_USE_QUERY, material_rows, NEO4J_BATCH_SIZE)
            if quantity_rows:
                client.run_batched(QUANTITY_QUERY, quantity_rows, NEO4J_BATCH_SIZE)
            if result_rows:
                client.run_batched(RESULT_QUERY, result_rows, NEO4J_BATCH_SIZE)
                client.run_batched(RESULT_LINK_QUERY, result_rows, NEO4J_BATCH_SIZE)
            # Switch the current pointer only after every dependent batch was
            # accepted. A failed rerun therefore leaves the previous run current.
            client.run(
                "MATCH (run:SustainabilityRun {id: $run_id}) "
                "OPTIONAL MATCH (old:SustainabilityRun {scopeKey: $scope_key, isCurrent: true}) "
                "WHERE old.id <> run.id "
                "FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END | "
                "SET old.isCurrent = false) "
                "SET run.isCurrent = true",
                {"run_id": run["id"], "scope_key": run["scope_key"]},
            )

    def get_run(
        self,
        project_id: str,
        file_ids: list[str],
        *,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        scope = sorted(file_ids)
        with self.client_factory() as client:
            rows = client.run(
                "MATCH (run:SustainabilityRun) "
                "WHERE run.projectId = $project_id "
                "AND ($run_id IS NULL OR run.id = $run_id) "
                "AND run.selectedFileIds = $file_ids "
                "AND ($run_id IS NOT NULL OR run.isCurrent = true) "
                "RETURN properties(run) AS run ORDER BY run.completedAt DESC LIMIT 1",
                {"project_id": project_id, "file_ids": scope, "run_id": run_id},
            )
        if not rows:
            return None
        run = dict(rows[0]["run"])
        run["project_id"] = run.get("projectId")
        run["file_ids"] = run.get("selectedFileIds") or []
        return run

    def get_results(self, run_id: str) -> list[dict[str, Any]]:
        with self.client_factory() as client:
            return client.run(
                "MATCH (:SustainabilityRun {id: $run_id})-[:HAS_RESULT]->(result:SustainabilityResult) "
                "RETURN properties(result) AS result ORDER BY result.source_ifc_file, result.element_id",
                {"run_id": run_id},
            )

    @staticmethod
    def unwrap_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(row.get("result", row)) for row in rows]


def cleanup_project_sustainability(client: Any, project_id: str) -> None:
    """Remove project-owned derived records before replacing its Element graph."""
    for label in ("SustainabilityResult", "QuantityEvidence", "MaterialUse", "SustainabilityRun"):
        client.run(
            f"MATCH (n:{label}) WHERE coalesce(n.projectId, n.project_id) = $project_id DETACH DELETE n",
            {"project_id": project_id},
        )
    client.run("MATCH (m:Material) WHERE NOT (m)<-[:OF_MATERIAL]-() DETACH DELETE m")
