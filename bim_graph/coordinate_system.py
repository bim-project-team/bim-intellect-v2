"""Inspect and conservatively validate IFC federation coordinate systems."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.util.placement as placement
import ifcopenshell.util.unit as unit


def _round_values(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, (list, tuple)):
        return [_round_values(item) for item in value]
    return value


def _matrix(axis_placement: Any) -> list[list[float]] | None:
    if axis_placement is None:
        return None
    try:
        return _round_values(placement.get_axis2placement(axis_placement).tolist())
    except Exception:
        return None


def _site_georef(site: Any) -> dict[str, Any] | None:
    latitude = list(site.RefLatitude) if getattr(site, "RefLatitude", None) else None
    longitude = list(site.RefLongitude) if getattr(site, "RefLongitude", None) else None
    elevation = getattr(site, "RefElevation", None)
    if latitude is None or longitude is None:
        return None
    return {"latitude": latitude, "longitude": longitude, "elevation": _round_values(elevation)}


def inspect_coordinate_system(path: str | Path) -> dict[str, Any]:
    model = ifcopenshell.open(str(path))
    contexts = []
    for context in model.by_type("IfcGeometricRepresentationContext"):
        if getattr(context, "ContextType", None) not in {None, "Model"}:
            continue
        contexts.append({
            "wcs": _matrix(getattr(context, "WorldCoordinateSystem", None)),
            "true_north": _round_values(list(context.TrueNorth.DirectionRatios))
            if getattr(context, "TrueNorth", None) else None,
        })
    sites = []
    for site in model.by_type("IfcSite"):
        sites.append({
            "guid": getattr(site, "GlobalId", None),
            "georef": _site_georef(site),
            "placement": _matrix(getattr(getattr(site, "ObjectPlacement", None), "RelativePlacement", None)),
        })
    map_conversions = []
    try:
        for conversion in model.by_type("IfcMapConversion"):
            map_conversions.append({
                key: _round_values(getattr(conversion, key, None))
                for key in ("Eastings", "Northings", "OrthogonalHeight", "XAxisAbscissa", "XAxisOrdinate", "Scale")
            })
    except RuntimeError:  # IFC2X3 has no IfcMapConversion entity
        pass
    metadata = {
        "schema": model.schema,
        "unit_scale_to_metre": float(unit.calculate_unit_scale(model)),
        "project_guids": sorted({project.GlobalId for project in model.by_type("IfcProject")}),
        "site_guids": sorted({site["guid"] for site in sites if site["guid"]}),
        "sites": sites,
        "contexts": contexts,
        "map_conversions": map_conversions,
        "geometry_mode": "ifcopenshell.USE_WORLD_COORDS",
    }
    canonical = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    metadata["coordinate_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metadata


def validate_federation(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"compatible": False, "status": "empty", "reason": "No IFC models were selected."}
    if len(records) == 1:
        return {"compatible": True, "status": "single_model", "reason": "A single IFC model needs no cross-file alignment check."}
    missing = [record.get("filename") or record.get("file_id") or "<unknown>"
               for record in records if not record.get("coordinate_system")]
    if missing:
        return {
            "compatible": False,
            "status": "missing_coordinate_metadata",
            "reason": (
                "Coordinate metadata is missing for one or more selected IFC files. "
                "Re-upload or re-scan them before cross-file analysis."
            ),
            "files": missing,
        }
    metadata = [record["coordinate_system"] for record in records]
    scales = [item["unit_scale_to_metre"] for item in metadata]
    if not all(math.isclose(scales[0], scale, rel_tol=1e-9, abs_tol=1e-12) for scale in scales[1:]):
        return {
            "compatible": False,
            "status": "unit_mismatch",
            "reason": "Selected IFC files use different project length units; cross-file coordinates were not mixed.",
            "unit_scales": scales,
        }
    common_projects = set(metadata[0]["project_guids"])
    common_sites = set(metadata[0]["site_guids"])
    for item in metadata[1:]:
        common_projects &= set(item["project_guids"])
        common_sites &= set(item["site_guids"])
    same_context = all(item["contexts"] == metadata[0]["contexts"] for item in metadata[1:])
    same_maps = bool(metadata[0]["map_conversions"]) and all(
        item["map_conversions"] == metadata[0]["map_conversions"] for item in metadata[1:]
    )
    georefs = [
        {json.dumps(site["georef"], sort_keys=True) for site in item["sites"] if site.get("georef")}
        for item in metadata
    ]
    common_georef = set.intersection(*georefs) if all(georefs) else set()
    if same_maps or (same_context and (common_projects or common_sites or common_georef)):
        basis = "map_conversion" if same_maps else (
            "shared_project_guid" if common_projects else "shared_site_reference"
        )
        return {"compatible": True, "status": "verified", "basis": basis, "reason": "Selected models share units and a verifiable IFC coordinate reference."}
    return {
        "compatible": False,
        "status": "unverified_alignment",
        "reason": (
            "The selected IFC files do not expose a shared project/site/georeference and matching world context. "
            "Cross-file clash detection was stopped to avoid false results. Federate/align the models in the authoring tool first."
        ),
    }
