"""Validated, deterministic carbon-factor repository backed by CSV."""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from .config import CARBON_FACTORS_PATH
from .models import CarbonFactor, FactorMatch
from .normalization import normalize_material_name
from .units import factor_quantity_type

REQUIRED_FIELDS = {
    "factor_id", "material_id", "material_name", "aliases", "category",
    "factor_value", "factor_unit", "region", "source", "source_version",
    "year", "dataset_version", "notes", "enabled",
}


class CarbonFactorDatasetError(ValueError):
    pass


def factor_record_id(dataset_hash: str, factor_id: str) -> str:
    """Content-version a graph factor identity while retaining its logical ID."""
    return hashlib.sha256(f"{dataset_hash}|{factor_id}".encode("utf-8")).hexdigest()


def _bool(value: str) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "enabled"}


class CarbonFactorRepository:
    def __init__(self, path: str | Path = CARBON_FACTORS_PATH):
        self.path = Path(path)
        self.dataset_hash = ""
        self.factors: list[CarbonFactor] = []
        self._index: dict[str, list[CarbonFactor]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise CarbonFactorDatasetError(f"Carbon-factor dataset not found: {self.path}")
        content = self.path.read_bytes()
        self.dataset_hash = hashlib.sha256(content).hexdigest()
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_FIELDS - set(reader.fieldnames or [])
            if missing:
                raise CarbonFactorDatasetError(
                    "Carbon-factor dataset is missing fields: " + ", ".join(sorted(missing))
                )
            seen_ids: set[str] = set()
            for line_number, row in enumerate(reader, start=2):
                if not any((value or "").strip() for value in row.values()):
                    continue
                factor_id = (row["factor_id"] or "").strip()
                if not factor_id or factor_id in seen_ids:
                    raise CarbonFactorDatasetError(f"Missing/duplicate factor_id at line {line_number}")
                seen_ids.add(factor_id)
                try:
                    value = float(row["factor_value"])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError
                    quantity_type = factor_quantity_type(row["factor_unit"])
                    year = int(row["year"]) if (row["year"] or "").strip() else None
                except (ValueError, TypeError) as exc:
                    raise CarbonFactorDatasetError(f"Invalid factor value/unit/year at line {line_number}") from exc
                source = (row["source"] or "").strip()
                source_version = (row["source_version"] or "").strip()
                dataset_version = (row["dataset_version"] or "").strip()
                material_id = (row["material_id"] or "").strip()
                material_name = (row["material_name"] or "").strip()
                category = (row["category"] or "").strip()
                region = (row["region"] or "").strip()
                if not all((material_id, material_name, category, region, source, source_version, dataset_version)):
                    raise CarbonFactorDatasetError(f"Missing factor provenance at line {line_number}")
                aliases = tuple(value.strip() for value in (row["aliases"] or "").split("|") if value.strip())
                factor = CarbonFactor(
                    factor_id=factor_id,
                    material_id=material_id,
                    material_name=material_name,
                    aliases=aliases,
                    category=category,
                    factor_value=value,
                    factor_unit=(row["factor_unit"] or "").strip(),
                    quantity_type=quantity_type,
                    region=region,
                    source=source,
                    source_version=source_version,
                    year=year,
                    dataset_version=dataset_version,
                    notes=(row["notes"] or "").strip(),
                    enabled=_bool(row["enabled"]),
                )
                self.factors.append(factor)
                if factor.enabled:
                    keys = {factor.material_id, factor.material_name, *factor.aliases}
                    for key in {normalize_material_name(item) for item in keys if item}:
                        self._index.setdefault(key, []).append(factor)

    @property
    def dataset_versions(self) -> list[str]:
        return sorted({factor.dataset_version for factor in self.factors})

    def match(
        self,
        raw_name: str,
        *,
        normalized_name: str | None = None,
        region: str | None = None,
        dataset_version: str | None = None,
        quantity_types: set[str] | None = None,
    ) -> FactorMatch:
        key = normalize_material_name(normalized_name or raw_name)
        candidates = list(self._index.get(key, []))
        if region:
            candidates = [item for item in candidates if item.region.casefold() == region.casefold()]
        if dataset_version:
            candidates = [item for item in candidates if item.dataset_version == dataset_version]
        if quantity_types:
            candidates = [item for item in candidates if item.quantity_type in quantity_types]
        if not candidates:
            return FactorMatch("unmatched")
        distinct = {item.factor_id: item for item in candidates}
        if len(distinct) > 1:
            return FactorMatch("ambiguous", candidates=tuple(sorted(distinct)))
        return FactorMatch("matched", factor=next(iter(distinct.values())))

    def status(self) -> dict:
        enabled = [item for item in self.factors if item.enabled]
        return {
            "path": str(self.path),
            "dataset_hash": self.dataset_hash,
            "dataset_versions": self.dataset_versions,
            "factor_count": len(self.factors),
            "enabled_factor_count": len(enabled),
            "enabled_dataset_versions": sorted({item.dataset_version for item in enabled}),
            "enabled_regions": sorted({item.region for item in enabled}),
            "dataset_ready_for_calculation": bool(enabled),
            # Source fields are mandatory, but software cannot certify a
            # dataset's environmental authority merely because rows exist.
            "authoritative": False,
        }

    def list_factors(self, *, dataset_version: str | None = None, region: str | None = None) -> list[dict]:
        factors = self.factors
        if dataset_version:
            factors = [item for item in factors if item.dataset_version == dataset_version]
        if region:
            factors = [item for item in factors if item.region.casefold() == region.casefold()]
        return [item.to_dict() for item in factors]
