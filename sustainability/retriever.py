"""Bounded deterministic sustainability evidence for hybrid chat."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .service import SustainabilityScopeError, SustainabilityService

SUSTAINABILITY_INTENTS = frozenset({
    "summary", "top_materials", "by_file", "top_elements",
    "missing_evidence", "leed_assessment", "general",
})


def normalize_sustainability_intent(value: str | None) -> str:
    intent = (value or "general").strip().casefold()
    return intent if intent in SUSTAINABILITY_INTENTS else "general"


def _carbon_values(value: Any, key: str = "") -> set[str]:
    output: set[str] = set()
    if isinstance(value, dict):
        for child_key, child in value.items():
            output.update(_carbon_values(child, str(child_key)))
    elif isinstance(value, list):
        for child in value:
            output.update(_carbon_values(child, key))
    elif value is not None and "carbon" in key.casefold() and "kgco2e" in key.casefold():
        try:
            output.add(format(float(value), ".15g"))
        except (TypeError, ValueError):
            pass
    return output


@dataclass
class SustainabilityEvidence:
    context: str = ""
    source: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    carbon_values: set[str] = field(default_factory=set)
    display_text: str = ""

    @property
    def available(self) -> bool:
        return bool(self.context and self.source)


class SustainabilityEvidenceRetriever:
    def __init__(self, service: SustainabilityService | None = None):
        self.service = service or SustainabilityService()

    def retrieve(
        self,
        question: str,
        *,
        project_id: str | None,
        file_ids: list[str] | None,
        intent: str | None = None,
    ) -> SustainabilityEvidence:
        selected_intent = normalize_sustainability_intent(intent)
        if not project_id:
            return SustainabilityEvidence(debug={
                "status": "missing_scope",
                "code": "project_scope_required",
                "message": "A project_id is required for deterministic sustainability evidence.",
                "intent": selected_intent,
            })
        try:
            summary = self.service.summary(project_id, file_ids)
            run = summary.get("run", {})
            data: dict[str, Any] = {
                "scope": {
                    "project_id": summary.get("project_id") or project_id,
                    "file_ids": summary.get("file_ids") or file_ids or [],
                    "run_id": run.get("id"),
                },
                "methodology_version": summary.get("methodology_version"),
                "factor_dataset_version": run.get("factorDatasetVersion"),
                "factor_dataset_hash": run.get("factorDatasetHash"),
                "factor_region": run.get("factorRegion"),
                "totals": {
                    key: summary.get(key) for key in (
                        "total_evaluated_elements", "elements_with_calculated_carbon",
                        "calculated_carbon_kgco2e", "explicit_carbon_kgco2e",
                        "estimated_carbon_kgco2e", "element_coverage_ratio",
                        "elements_missing_material", "elements_missing_quantity",
                        "unmatched_materials", "ambiguous_mappings",
                        "explicit_quantity_results", "estimated_quantity_results",
                    )
                },
                "calculation_status_counts": summary.get("calculation_status_counts", {}),
                "by_material": summary.get("by_material", [])[:15],
                "by_source_ifc_file": summary.get("by_source_ifc_file", [])[:15],
                "by_discipline": summary.get("by_discipline", [])[:15],
                "by_ifc_type": summary.get("by_ifc_type", [])[:15],
            }
            if selected_intent in {"top_materials", "general", "leed_assessment"}:
                data["material_results"] = self.service.materials(
                    project_id, file_ids, run_id=run.get("id")
                ).get("items", [])[:25]
            if selected_intent == "top_elements":
                data["element_results"] = self.service.elements(
                    project_id, file_ids, run_id=run.get("id"), limit=25
                ).get("items", [])
            if selected_intent in {"missing_evidence", "leed_assessment"}:
                missing: dict[str, list[dict]] = {}
                for status in (
                    "missing_material", "missing_quantity", "unmatched_material",
                    "ambiguous_mapping", "ambiguous_quantity", "incompatible_unit",
                ):
                    rows = self.service.elements(
                        project_id, file_ids, run_id=run.get("id"), status=status, limit=25
                    ).get("items", [])
                    if rows:
                        missing[status] = rows
                data["excluded_elements"] = missing
        except SustainabilityScopeError as exc:
            return SustainabilityEvidence(debug={
                "status": "unavailable", "code": exc.code,
                "message": str(exc), "intent": selected_intent,
            })

        context = (
            "<SUSTAINABILITY_EVIDENCE deterministic=\"true\">\n"
            "The values below are stored deterministic results. Copy them exactly; do not recalculate.\n"
            + json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n</SUSTAINABILITY_EVIDENCE>"
        )
        totals = data["totals"]
        lines = [
            f"Deterministic sustainability run {data['scope']['run_id']} for project {data['scope']['project_id']}.",
            f"Calculated embodied carbon: {totals['calculated_carbon_kgco2e']} kgCO2e.",
            f"Evaluated elements: {totals['total_evaluated_elements']}; elements with calculated carbon: {totals['elements_with_calculated_carbon']}.",
        ]
        if data["by_material"]:
            top = data["by_material"][0]
            lines.append(f"Largest calculated material group: {top['key']} — {top['carbon_kgco2e']} kgCO2e.")
        source = {
            "type": "sustainability",
            "id": data["scope"]["run_id"],
            "name": "Deterministic sustainability analysis",
            "project_id": data["scope"]["project_id"],
            "file_ids": data["scope"]["file_ids"],
            "methodology_version": data["methodology_version"],
            "factor_dataset_version": data["factor_dataset_version"],
            "factor_dataset_hash": data["factor_dataset_hash"],
        }
        return SustainabilityEvidence(
            context=context,
            source=source,
            data=data,
            debug={"status": "available", "intent": selected_intent, "run_id": source["id"]},
            carbon_values=_carbon_values(data),
            display_text="\n".join(lines),
        )
