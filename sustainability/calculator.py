"""Deterministic, dimension-checked embodied-carbon calculation and summaries."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

from .config import METHODOLOGY_VERSION, QUANTITY_NAME_PRIORITY
from .factors import CarbonFactorRepository, factor_record_id
from .models import CarbonResult, ElementEvidence, MaterialUse, QuantityEvidence
from .units import dimensions_compatible


def _result_id(run_id: str, element_id: str, material_use_id: str | None) -> str:
    return hashlib.sha256(f"{run_id}|{element_id}|{material_use_id or 'missing'}".encode()).hexdigest()


def select_quantity(quantities: list[QuantityEvidence], quantity_type: str) -> tuple[QuantityEvidence | None, str]:
    valid = [
        item for item in quantities
        if item.quantity_type == quantity_type and item.normalized_value is not None and not item.error
    ]
    if not valid:
        return None, "missing_quantity"
    source_order = {"ifc_explicit": 0, "geometry_derived": 1}
    level_order = {"occurrence": 0, "type": 1}
    names = QUANTITY_NAME_PRIORITY.get(quantity_type, ())
    name_order = {name.casefold(): index for index, name in enumerate(names)}
    valid.sort(key=lambda item: (
        source_order.get(item.quantity_source, 9),
        level_order.get(item.association_level, 9),
        name_order.get(item.quantity_name.casefold(), len(name_order) + 1),
        item.quantity_set_name.casefold(), item.id,
    ))
    best = valid[0]
    best_rank = (
        source_order.get(best.quantity_source, 9),
        level_order.get(best.association_level, 9),
        name_order.get(best.quantity_name.casefold(), len(name_order) + 1),
    )
    peers = [item for item in valid if (
        source_order.get(item.quantity_source, 9),
        level_order.get(item.association_level, 9),
        name_order.get(item.quantity_name.casefold(), len(name_order) + 1),
    ) == best_rank]
    distinct_values = {round(float(item.normalized_value), 12) for item in peers}
    if len(distinct_values) > 1:
        return None, "ambiguous_quantity"
    return best, "selected"


def _allocation_share(material: MaterialUse, materials: list[MaterialUse], quantity_type: str) -> tuple[float | None, str]:
    if len(materials) == 1:
        return 1.0, "whole_element_single_material"
    if quantity_type == "volume" and all(
        item.component_type == "layer" and item.layer_thickness_m is not None
        and math.isfinite(item.layer_thickness_m) and item.layer_thickness_m > 0
        for item in materials
    ):
        total = sum(float(item.layer_thickness_m) for item in materials)
        return float(material.layer_thickness_m) / total, "layer_thickness_ratio"
    if all(
        item.component_type == "constituent" and item.fraction is not None
        and math.isfinite(item.fraction) and item.fraction >= 0
        for item in materials
    ):
        total = sum(float(item.fraction) for item in materials)
        if total > 0:
            return float(material.fraction) / total, "authored_constituent_fraction"
    return None, "unsupported_multi_material_allocation"


def _base_result(element: ElementEvidence, material: MaterialUse | None, run_id: str) -> dict[str, Any]:
    return {
        "id": _result_id(run_id, element.element_id, material.id if material else None),
        "run_id": run_id,
        "element_id": element.element_id,
        "ifc_guid": element.ifc_guid,
        "ifc_type": element.ifc_type,
        "element_name": element.name,
        "storey_name": element.storey_name,
        "project_id": element.project_id,
        "source_file_id": element.source_file_id,
        "source_ifc_file": element.source_ifc_file,
        "discipline": element.discipline,
        "material_use_id": material.id if material else None,
        "material_raw": material.raw_name if material else None,
        "material_normalized": material.normalized_name if material else None,
        "material_category": material.category if material else None,
        "mapping_status": "unmatched",
        "raw_quantity": None,
        "raw_quantity_unit": None,
        "normalized_quantity": None,
        "normalized_quantity_unit": None,
        "quantity_type": None,
        "quantity_source": "unavailable",
        "factor_id": None,
        "factor_value": None,
        "factor_unit": None,
        "factor_source": None,
        "factor_source_version": None,
        "carbon_kgco2e": None,
        "calculation_status": "missing_material" if material is None else "unmatched_material",
        "quantity_evidence_id": None,
        "allocation_share": None,
        "factor_region": None,
        "factor_year": None,
        "factor_dataset_version": None,
        "factor_dataset_hash": None,
        "factor_record_id": None,
        "methodology_version": METHODOLOGY_VERSION,
        "calculation_note": "",
    }


def calculate_elements(
    elements: list[ElementEvidence],
    factors: CarbonFactorRepository,
    *,
    run_id: str,
    region: str | None = None,
    dataset_version: str | None = None,
) -> list[CarbonResult]:
    results: list[CarbonResult] = []
    for element in elements:
        if not element.materials:
            values = _base_result(element, None, run_id)
            values["calculation_note"] = "No supported IFC material association was found."
            results.append(CarbonResult(**values))
            continue
        for material in element.materials:
            values = _base_result(element, material, run_id)
            match = factors.match(
                material.raw_name,
                normalized_name=material.normalized_name,
                region=region,
                dataset_version=dataset_version,
            )
            # Quantity dimension may disambiguate multiple equally valid
            # factors, but it must not turn a uniquely matched mass factor
            # into an "unmatched material" merely because mass is missing.
            if match.status == "ambiguous":
                match = factors.match(
                    material.raw_name,
                    normalized_name=material.normalized_name,
                    region=region,
                    dataset_version=dataset_version,
                    quantity_types={
                    item.quantity_type for item in element.quantities
                    if item.normalized_value is not None and not item.error
                    },
                )
            values["mapping_status"] = match.status
            if match.status != "matched" or match.factor is None:
                values["calculation_status"] = "ambiguous_mapping" if match.status == "ambiguous" else "unmatched_material"
                values["calculation_note"] = (
                    "Candidate factor IDs: " + ", ".join(match.candidates)
                    if match.candidates else "No enabled exact material/alias factor matched."
                )
                results.append(CarbonResult(**values))
                continue
            factor = match.factor
            dataset_hash = getattr(factors, "dataset_hash", "unversioned")
            values.update({
                "factor_id": factor.factor_id,
                "factor_value": factor.factor_value,
                "factor_unit": factor.factor_unit,
                "factor_source": factor.source,
                "factor_source_version": factor.source_version,
                "factor_region": factor.region,
                "factor_year": factor.year,
                "factor_dataset_version": factor.dataset_version,
                "factor_dataset_hash": dataset_hash,
                "factor_record_id": factor_record_id(dataset_hash, factor.factor_id),
            })
            quantity, quantity_status = select_quantity(element.quantities, factor.quantity_type)
            if quantity is None:
                values["calculation_status"] = quantity_status
                values["calculation_note"] = f"No unambiguous {factor.quantity_type} quantity is available."
                results.append(CarbonResult(**values))
                continue
            if not dimensions_compatible(quantity.quantity_type, factor.factor_unit):
                values["calculation_status"] = "incompatible_unit"
                values["calculation_note"] = "Quantity dimension and factor denominator are incompatible."
                results.append(CarbonResult(**values))
                continue
            share, allocation = _allocation_share(material, element.materials, quantity.quantity_type)
            if share is None:
                values["calculation_status"] = "missing_quantity"
                values["calculation_note"] = allocation
                results.append(CarbonResult(**values))
                continue
            normalized_quantity = float(quantity.normalized_value) * share
            carbon = Decimal(str(normalized_quantity)) * Decimal(str(factor.factor_value))
            quantity_source = quantity.quantity_source
            status = "calculated_explicit"
            if quantity_source != "ifc_explicit" or share != 1.0:
                status = "calculated_estimate"
            values.update({
                "raw_quantity": quantity.quantity_value,
                "raw_quantity_unit": quantity.quantity_unit,
                "normalized_quantity": normalized_quantity,
                "normalized_quantity_unit": quantity.normalized_unit,
                "quantity_type": quantity.quantity_type,
                "quantity_source": quantity_source,
                "quantity_evidence_id": quantity.id,
                "allocation_share": share,
                "carbon_kgco2e": float(carbon),
                "calculation_status": status,
                "calculation_note": allocation,
            })
            results.append(CarbonResult(**values))
    return results


def _breakdown(rows: list[dict], field: str) -> list[dict]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "carbon_kgco2e": Decimal("0"), "result_count": 0,
        "calculated_results": 0, "element_ids": set(),
        "calculated_element_ids": set(), "statuses": defaultdict(int),
    })
    for row in rows:
        key = str(row.get(field) or "unknown")
        item = grouped[key]
        item["result_count"] += 1
        item["element_ids"].add(row["element_id"])
        item["statuses"][str(row.get("calculation_status"))] += 1
        if row.get("carbon_kgco2e") is not None:
            item["carbon_kgco2e"] += Decimal(str(row["carbon_kgco2e"]))
            item["calculated_results"] += 1
            item["calculated_element_ids"].add(row["element_id"])
    return sorted(({
        "key": key,
        "carbon_kgco2e": float(value["carbon_kgco2e"]),
        "result_count": value["result_count"],
        "calculated_results": value["calculated_results"],
        "element_count": len(value["element_ids"]),
        "calculated_element_count": len(value["calculated_element_ids"]),
        "element_coverage_ratio": (
            len(value["calculated_element_ids"]) / len(value["element_ids"])
            if value["element_ids"] else 0.0
        ),
        "calculation_status_counts": dict(sorted(value["statuses"].items())),
    } for key, value in grouped.items()), key=lambda item: item["carbon_kgco2e"], reverse=True)


def summarize_results(results: list[CarbonResult | dict], run: dict | None = None) -> dict[str, Any]:
    rows = [item.to_dict() if isinstance(item, CarbonResult) else dict(item) for item in results]
    element_ids = {row["element_id"] for row in rows}
    calculated = [row for row in rows if row.get("carbon_kgco2e") is not None]
    calculated_elements = {row["element_id"] for row in calculated}
    explicit = [row for row in calculated if row.get("calculation_status") == "calculated_explicit"]
    estimated = [row for row in calculated if row.get("calculation_status") == "calculated_estimate"]
    explicit_elements = {row["element_id"] for row in explicit}
    estimated_elements = {row["element_id"] for row in estimated}
    total = sum((Decimal(str(row["carbon_kgco2e"])) for row in calculated), Decimal("0"))
    explicit_total = sum((Decimal(str(row["carbon_kgco2e"])) for row in explicit), Decimal("0"))
    estimated_total = sum((Decimal(str(row["carbon_kgco2e"])) for row in estimated), Decimal("0"))
    statuses = defaultdict(int)
    for row in rows:
        statuses[str(row.get("calculation_status"))] += 1
    summary = {
        "methodology_version": METHODOLOGY_VERSION,
        "total_evaluated_elements": len(element_ids),
        "elements_with_calculated_carbon": len(calculated_elements),
        "elements_not_evaluated": len(element_ids - calculated_elements),
        "elements_with_explicit_quantities": len(explicit_elements),
        "elements_with_estimated_quantities": len(estimated_elements),
        "explicit_quantity_coverage_ratio": (
            len(explicit_elements) / len(element_ids) if element_ids else 0.0
        ),
        "estimated_quantity_coverage_ratio": (
            len(estimated_elements) / len(element_ids) if element_ids else 0.0
        ),
        "elements_missing_material": len({row["element_id"] for row in rows if row.get("calculation_status") == "missing_material"}),
        "elements_missing_quantity": len({row["element_id"] for row in rows if row.get("calculation_status") in {"missing_quantity", "ambiguous_quantity"}}),
        "unmatched_materials": len({row.get("material_raw") for row in rows if row.get("calculation_status") == "unmatched_material" and row.get("material_raw")}),
        "ambiguous_mappings": sum(1 for row in rows if row.get("calculation_status") == "ambiguous_mapping"),
        "explicit_quantity_results": len(explicit),
        "estimated_quantity_results": len(estimated),
        "calculated_carbon_kgco2e": float(total),
        "explicit_carbon_kgco2e": float(explicit_total),
        "estimated_carbon_kgco2e": float(estimated_total),
        "element_coverage_ratio": (len(calculated_elements) / len(element_ids)) if element_ids else 0.0,
        "calculation_status_counts": dict(sorted(statuses.items())),
        "by_project": _breakdown(rows, "project_id"),
        "by_source_ifc_file": _breakdown(rows, "source_ifc_file"),
        "by_discipline": _breakdown(rows, "discipline"),
        "by_ifc_type": _breakdown(rows, "ifc_type"),
        "by_material": _breakdown(rows, "material_normalized"),
        "by_element": _breakdown(rows, "element_id"),
        "limitations": [
            "Results depend on the supplied carbon-factor data and its provenance.",
            "Geometry-derived quantities are AABB envelope estimates, not exact IFC quantities.",
            "Unknown and unmatched records are excluded from the carbon total, not treated as zero.",
        ],
    }
    if run:
        summary["run"] = dict(run)
        summary["project_id"] = run.get("project_id") or run.get("projectId")
        summary["file_ids"] = run.get("file_ids") or run.get("selectedFileIds") or []
    return summary
