"""Typed records shared by sustainability extraction, calculation, and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import METHODOLOGY_VERSION


@dataclass
class MaterialUse:
    id: str
    raw_name: str
    normalized_name: str
    category: str
    association_type: str
    association_level: str = "occurrence"
    component_type: str = "material"
    component_index: int = 0
    layer_thickness_m: float | None = None
    fraction: float | None = None
    material_ifc_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuantityEvidence:
    id: str
    quantity_value: float | None
    quantity_type: str
    quantity_unit: str
    quantity_source: str
    normalized_value: float | None = None
    normalized_unit: str | None = None
    quantity_name: str = ""
    quantity_set_name: str = ""
    association_level: str = "occurrence"
    derivation_method: str = ""
    quality: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ElementEvidence:
    element_id: str
    ifc_guid: str
    ifc_type: str
    name: str
    storey_name: str
    project_id: str
    source_file_id: str
    source_ifc_file: str
    discipline: str
    materials: list[MaterialUse] = field(default_factory=list)
    quantities: list[QuantityEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class CarbonFactor:
    factor_id: str
    material_id: str
    material_name: str
    aliases: tuple[str, ...]
    category: str
    factor_value: float
    factor_unit: str
    quantity_type: str
    region: str
    source: str
    source_version: str
    year: int | None
    dataset_version: str
    notes: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["aliases"] = list(self.aliases)
        return value


@dataclass(frozen=True)
class FactorMatch:
    status: str
    factor: CarbonFactor | None = None
    candidates: tuple[str, ...] = ()


@dataclass
class CarbonResult:
    id: str
    run_id: str
    element_id: str
    ifc_guid: str
    ifc_type: str
    element_name: str
    storey_name: str
    project_id: str
    source_file_id: str
    source_ifc_file: str
    discipline: str
    material_use_id: str | None
    material_raw: str | None
    material_normalized: str | None
    material_category: str | None
    mapping_status: str
    raw_quantity: float | None
    raw_quantity_unit: str | None
    normalized_quantity: float | None
    normalized_quantity_unit: str | None
    quantity_type: str | None
    quantity_source: str
    factor_id: str | None
    factor_value: float | None
    factor_unit: str | None
    factor_source: str | None
    factor_source_version: str | None
    carbon_kgco2e: float | None
    calculation_status: str
    quantity_evidence_id: str | None = None
    allocation_share: float | None = None
    factor_region: str | None = None
    factor_year: int | None = None
    factor_dataset_version: str | None = None
    factor_dataset_hash: str | None = None
    factor_record_id: str | None = None
    methodology_version: str = METHODOLOGY_VERSION
    calculation_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
