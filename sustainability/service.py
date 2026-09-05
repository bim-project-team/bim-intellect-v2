"""Project/file-scoped sustainability analysis orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from bim_graph.coordinate_system import validate_federation
from bim_graph.project_registry import get_files, safe_id, update_file

from .calculator import calculate_elements, summarize_results
from .config import CARBON_FACTORS_PATH, EXTRACTOR_VERSION, METHODOLOGY_VERSION
from .factors import CarbonFactorRepository
from .ifc_extractor import extract_file_evidence
from .repository import SustainabilityRepository


class SustainabilityScopeError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid_scope"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_key(project_id: str, file_ids: list[str]) -> str:
    canonical = json.dumps([project_id, *sorted(file_ids)], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class SustainabilityService:
    def __init__(
        self,
        *,
        repository: SustainabilityRepository | None = None,
        factor_path=CARBON_FACTORS_PATH,
        file_extractor: Callable = extract_file_evidence,
    ):
        self.repository = repository or SustainabilityRepository()
        self.factor_path = factor_path
        self.file_extractor = file_extractor

    def resolve_scope(self, project_id: str, file_ids: list[str] | None) -> tuple[str, list[dict[str, Any]]]:
        project_id = safe_id(project_id)
        all_records = get_files(project_id)
        if file_ids:
            wanted = set(file_ids)
            records = [item for item in all_records if item["file_id"] in wanted]
            missing = sorted(wanted - {item["file_id"] for item in records})
            if missing:
                raise SustainabilityScopeError(
                    f"Unknown file IDs for project {project_id}: {missing}", status_code=404,
                )
        else:
            records = [item for item in all_records if item.get("status") == "ingested"]
        if not records:
            raise SustainabilityScopeError("No ingested IFC models are available in the requested scope.", status_code=404)
        not_ingested = [item["filename"] for item in records if item.get("status") != "ingested"]
        if not_ingested:
            raise SustainabilityScopeError(
                f"Models are not imported into the graph: {not_ingested}", status_code=409,
            )
        alignment = validate_federation(records)
        if not alignment["compatible"]:
            raise SustainabilityScopeError(alignment["reason"], status_code=409, code=alignment["status"])
        return project_id, sorted(records, key=lambda item: item["file_id"])

    def analyze(
        self,
        project_id: str,
        file_ids: list[str] | None = None,
        *,
        factor_dataset_version: str | None = None,
        region: str | None = None,
        allow_geometry_derived: bool = False,
    ) -> dict[str, Any]:
        project_id, records = self.resolve_scope(project_id, file_ids)
        started_at = _utc_now()
        resolved_file_ids = [item["file_id"] for item in records]
        graph_elements = self.repository.fetch_elements(project_id, resolved_file_ids)
        if not graph_elements:
            raise SustainabilityScopeError(
                "No retained semantic elements were found in Neo4j for the requested scope.",
                status_code=409, code="empty_graph_scope",
            )
        factors = CarbonFactorRepository(self.factor_path)
        enabled_factors = [item for item in factors.factors if item.enabled]
        enabled_versions = sorted({item.dataset_version for item in enabled_factors})
        if factor_dataset_version and factor_dataset_version not in enabled_versions:
            raise SustainabilityScopeError(
                f"Carbon-factor dataset version {factor_dataset_version!r} is unavailable.",
                status_code=409, code="factor_dataset_version_unavailable",
            )
        selected_version = factor_dataset_version
        if selected_version is None and len(enabled_versions) == 1:
            selected_version = enabled_versions[0]
        elif selected_version is None and len(enabled_versions) > 1:
            raise SustainabilityScopeError(
                "Multiple enabled carbon-factor dataset versions are available; select one explicitly.",
                status_code=409, code="factor_dataset_version_required",
            )
        applicable_factors = [
            item for item in enabled_factors
            if selected_version is None or item.dataset_version == selected_version
        ]
        enabled_regions = sorted({item.region for item in applicable_factors})
        region_lookup = {item.casefold(): item for item in enabled_regions}
        if region and region.casefold() not in region_lookup:
            raise SustainabilityScopeError(
                f"Carbon-factor region {region!r} is unavailable in the selected dataset.",
                status_code=409, code="factor_region_unavailable",
            )
        selected_region = region_lookup.get(region.casefold()) if region else None
        if selected_region is None and len(enabled_regions) == 1:
            selected_region = enabled_regions[0]
        elif selected_region is None and len(enabled_regions) > 1:
            raise SustainabilityScopeError(
                "Multiple carbon-factor regions are available; select one explicitly.",
                status_code=409, code="factor_region_required",
            )
        by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in graph_elements:
            by_file[str(item["source_file_id"])].append(item)
        evidence = []
        for record in records:
            evidence.extend(self.file_extractor(
                record["stored_path"], by_file.get(record["file_id"], []), record,
                allow_geometry_derived=allow_geometry_derived,
            ))
        run_id = str(uuid.uuid4())
        results = calculate_elements(
            evidence, factors, run_id=run_id, region=selected_region,
            dataset_version=selected_version,
        )
        now = _utc_now()
        run = {
            "id": run_id,
            "scope_key": _scope_key(project_id, resolved_file_ids),
            "scopeKey": _scope_key(project_id, resolved_file_ids),
            "project_id": project_id,
            "file_ids": resolved_file_ids,
            "status": "completed",
            "isCurrent": True,
            "startedAt": started_at,
            "completedAt": now,
            "methodologyVersion": METHODOLOGY_VERSION,
            "extractorVersion": EXTRACTOR_VERSION,
            "factorDatasetVersion": selected_version or "unspecified",
            "factorDatasetHash": factors.dataset_hash,
            "factorRegion": selected_region or "unspecified",
            "allowGeometryDerived": allow_geometry_derived,
        }
        summary = summarize_results(results, run)
        run.update({
            "elementCount": summary["total_evaluated_elements"],
            "calculatedElementCount": summary["elements_with_calculated_carbon"],
            "totalCarbonKgCO2e": summary["calculated_carbon_kgco2e"],
        })
        summary["run"] = dict(run)
        self.repository.persist(run, evidence, results, factors.factors)
        for record in records:
            update_file(
                project_id, record["file_id"], sustainability_status="analyzed",
                sustainability_run_id=run_id, sustainability_analyzed_at=now,
            )
        summary["factor_repository"] = factors.status()
        return summary

    def _load(self, project_id: str, file_ids: list[str] | None, run_id: str | None = None):
        project_id, records = self.resolve_scope(project_id, file_ids)
        resolved = [item["file_id"] for item in records]
        run = self.repository.get_run(project_id, resolved, run_id=run_id)
        if not run:
            raise SustainabilityScopeError(
                "No completed sustainability analysis exists for the requested scope.",
                status_code=404, code="analysis_not_found",
            )
        rows = self.repository.unwrap_results(self.repository.get_results(run["id"]))
        return run, rows

    def summary(self, project_id: str, file_ids: list[str] | None, run_id: str | None = None) -> dict:
        run, rows = self._load(project_id, file_ids, run_id)
        return summarize_results(rows, run)

    def elements(
        self, project_id: str, file_ids: list[str] | None, *, run_id: str | None = None,
        status: str | None = None, ifc_type: str | None = None, limit: int = 100, offset: int = 0,
    ) -> dict:
        run, rows = self._load(project_id, file_ids, run_id)
        if status:
            rows = [item for item in rows if item.get("calculation_status") == status]
        if ifc_type:
            rows = [item for item in rows if item.get("ifc_type") == ifc_type]
        rows.sort(key=lambda item: (-(item.get("carbon_kgco2e") or -1), item.get("element_id", "")))
        return {"run": run, "total": len(rows), "limit": limit, "offset": offset, "items": rows[offset:offset + limit]}

    def materials(self, project_id: str, file_ids: list[str] | None, *, run_id: str | None = None) -> dict:
        run, rows = self._load(project_id, file_ids, run_id)
        grouped: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            key = (
                row.get("material_normalized") or "missing",
                row.get("mapping_status") or "unmatched",
                row.get("factor_id"),
            )
            item = grouped.setdefault(key, {
                "material_normalized": key[0], "mapping_status": key[1],
                "factor_id": key[2], "factor_unit": row.get("factor_unit"),
                "factor_source": row.get("factor_source"), "element_ids": set(),
                "raw_names": set(), "carbon_kgco2e": 0.0,
            })
            item["element_ids"].add(row["element_id"])
            if row.get("material_raw"):
                item["raw_names"].add(row["material_raw"])
            if row.get("carbon_kgco2e") is not None:
                item["carbon_kgco2e"] += float(row["carbon_kgco2e"])
        items = []
        for item in grouped.values():
            item["element_count"] = len(item.pop("element_ids"))
            item["raw_names"] = sorted(item["raw_names"])
            items.append(item)
        items.sort(key=lambda item: (-item["carbon_kgco2e"], item["material_normalized"]))
        return {"run": run, "total": len(items), "items": items}

    def factors(self, *, dataset_version: str | None = None, region: str | None = None) -> dict:
        repository = CarbonFactorRepository(self.factor_path)
        return {**repository.status(), "items": repository.list_factors(dataset_version=dataset_version, region=region)}
