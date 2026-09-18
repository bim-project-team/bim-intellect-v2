"""Inspect and conservatively validate IFC federation coordinate systems."""

from __future__ import annotations

import hashlib
import json
import math
import re
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


def _object_placement_matrix(object_placement: Any) -> list[list[float]] | None:
    if object_placement is None:
        return None
    try:
        return _round_values(placement.get_local_placement(object_placement).tolist())
    except Exception:
        return None


def _site_georef(site: Any) -> dict[str, Any] | None:
    latitude = list(site.RefLatitude) if getattr(site, "RefLatitude", None) else None
    longitude = list(site.RefLongitude) if getattr(site, "RefLongitude", None) else None
    elevation = getattr(site, "RefElevation", None)
    if latitude is None or longitude is None:
        return None
    return {"latitude": latitude, "longitude": longitude, "elevation": _round_values(elevation)}


def _spatial_record(entity: Any, *, include_georef: bool = False) -> dict[str, Any]:
    value = {
        "guid": getattr(entity, "GlobalId", None),
        "name": getattr(entity, "Name", None),
        "long_name": getattr(entity, "LongName", None),
        "placement": _object_placement_matrix(getattr(entity, "ObjectPlacement", None)),
    }
    if include_georef:
        value["georef"] = _site_georef(entity)
    return value


def _named_record(entity: Any) -> dict[str, Any]:
    return {
        "guid": getattr(entity, "GlobalId", None),
        "name": getattr(entity, "Name", None),
        "long_name": getattr(entity, "LongName", None),
    }


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
    projects = [_named_record(project) for project in model.by_type("IfcProject")]
    sites = [_spatial_record(site, include_georef=True) for site in model.by_type("IfcSite")]
    buildings = [_spatial_record(building) for building in model.by_type("IfcBuilding")]
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
        "project_guids": sorted({project["guid"] for project in projects if project["guid"]}),
        "site_guids": sorted({site["guid"] for site in sites if site["guid"]}),
        "projects": projects,
        "sites": sites,
        "buildings": buildings,
        "contexts": contexts,
        "map_conversions": map_conversions,
        "geometry_mode": "ifcopenshell.USE_WORLD_COORDS",
    }
    canonical = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    metadata["coordinate_fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metadata


_LABEL_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)
_NON_IDENTIFYING_LABELS = {
    "building", "default building", "default project", "project", "site", "unnamed",
}
_FRAME_ABS_TOLERANCE = 1e-6
_PLACEMENT_TRANSLATION_TOLERANCE_METRES = 0.02


def _metadata_for_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return current coordinate metadata, upgrading legacy registry entries in memory.

    Older uploads predate project/building semantic records. Re-reading only those
    files keeps existing projects usable without weakening validation or requiring a
    destructive registry migration.
    """
    metadata = record.get("coordinate_system")
    if metadata and "projects" in metadata and "buildings" in metadata:
        return metadata
    stored_path = record.get("stored_path")
    if stored_path:
        path = Path(stored_path)
        if path.is_file():
            try:
                metadata = inspect_coordinate_system(path)
                record["coordinate_system"] = metadata
                return metadata
            except (OSError, RuntimeError, ValueError):
                # The caller reports missing/invalid metadata without exposing a
                # filesystem path or a parser implementation detail to the client.
                pass
    return metadata


def _normalized_label(value: Any) -> str:
    return _LABEL_SEPARATORS.sub(" ", str(value or "").casefold()).strip()


def _entity_labels(metadata: dict[str, Any], key: str) -> set[str]:
    labels: set[str] = set()
    entities = metadata.get(key, [])
    if not isinstance(entities, (list, tuple)):
        return labels
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        for field in ("name", "long_name"):
            label = _normalized_label(entity.get(field))
            if label and label not in _NON_IDENTIFYING_LABELS:
                labels.add(label)
    return labels


def _string_values(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item) for item in value if item}


def _dict_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _shared_values(groups: list[set[str]]) -> set[str]:
    return set.intersection(*groups) if groups and all(groups) else set()


def _finite_matrix(value: Any, scale: float = 1.0) -> tuple[tuple[float, ...], ...] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    matrix: list[tuple[float, ...]] = []
    width = None
    try:
        for row in value:
            if not isinstance(row, (list, tuple)) or not row:
                return None
            numbers = [float(item) for item in row]
            if not all(math.isfinite(item) for item in numbers):
                return None
            width = width or len(numbers)
            if len(numbers) != width:
                return None
            matrix.append(tuple(numbers))
    except (TypeError, ValueError):
        return None
    # IFC placement matrices are 4x4. Convert only their translation column
    # to metres; rotation/scale coefficients must stay dimensionless.
    if len(matrix) == 4 and width == 4:
        matrix = [
            tuple(item * scale if column == 3 and row_index < 3 else item
                  for column, item in enumerate(row))
            for row_index, row in enumerate(matrix)
        ]
    return tuple(matrix)


def _matrix_close(
    left: tuple[tuple[float, ...], ...],
    right: tuple[tuple[float, ...], ...],
    *,
    translation_tolerance: float = _FRAME_ABS_TOLERANCE,
) -> bool:
    if len(left) != len(right) or any(len(a) != len(b) for a, b in zip(left, right)):
        return False
    is_ifc_matrix = len(left) == 4 and all(len(row) == 4 for row in left)
    for row_index, (left_row, right_row) in enumerate(zip(left, right)):
        for column, (a, b) in enumerate(zip(left_row, right_row)):
            tolerance = (
                translation_tolerance
                if is_ifc_matrix and column == 3 and row_index < 3
                else _FRAME_ABS_TOLERANCE
            )
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=tolerance):
                return False
    return True


def _matrix_group(metadata: dict[str, Any], source: str) -> list[tuple[tuple[float, ...], ...]]:
    scale = float(metadata["unit_scale_to_metre"])
    if source == "contexts":
        values = [item.get("wcs") for item in _dict_records(metadata.get("contexts"))]
    else:
        values = [item.get("placement") for item in _dict_records(metadata.get(source))]
    output: list[tuple[tuple[float, ...], ...]] = []
    for value in values:
        matrix = _finite_matrix(value, scale)
        if matrix is not None and not any(_matrix_close(matrix, saved) for saved in output):
            output.append(matrix)
    return output


def _groups_share_matrix(
    groups: list[list[tuple[tuple[float, ...], ...]]],
    *,
    translation_tolerance: float,
) -> bool:
    if not groups or not all(groups):
        return False
    candidates = groups[0]
    return any(
        all(any(_matrix_close(candidate, other, translation_tolerance=translation_tolerance)
                for other in group) for group in groups[1:])
        for candidate in candidates
    )


def _true_north_values(metadata: dict[str, Any]) -> list[tuple[float, ...]]:
    output = []
    for context in _dict_records(metadata.get("contexts")):
        value = context.get("true_north")
        if not isinstance(value, (list, tuple)):
            continue
        try:
            direction = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            continue
        if direction and all(math.isfinite(item) for item in direction):
            output.append(direction)
    return output


def _directions_compatible(metadata: list[dict[str, Any]]) -> bool:
    groups = [_true_north_values(item) for item in metadata]
    known = [group for group in groups if group]
    if len(known) < 2:
        return True
    return any(
        all(any(
            len(candidate) == len(other)
            and all(math.isclose(x, y, abs_tol=1e-6) for x, y in zip(candidate, other))
            for other in group
        ) for group in known[1:])
        for candidate in known[0]
    )


def _canonical_georefs(metadata: dict[str, Any]) -> set[str]:
    return {
        json.dumps(site["georef"], sort_keys=True)
        for site in _dict_records(metadata.get("sites"))
        if site.get("georef")
    }


def _canonical_maps(metadata: dict[str, Any]) -> set[str]:
    return {
        json.dumps(item, sort_keys=True)
        for item in _dict_records(metadata.get("map_conversions"))
        if item
    }


def validate_federation(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"compatible": False, "status": "empty", "reason": "No IFC models were selected."}
    if len(records) == 1:
        return {"compatible": True, "status": "single_model", "reason": "A single IFC model needs no cross-file alignment check."}
    metadata = [_metadata_for_record(record) for record in records]
    missing = [
        record.get("filename") or record.get("file_id") or "<unknown>"
        for record, item in zip(records, metadata) if not item
    ]
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
    metadata = [item for item in metadata if item is not None]
    try:
        scales = [float(item["unit_scale_to_metre"]) for item in metadata]
    except (KeyError, TypeError, ValueError):
        return {
            "compatible": False,
            "status": "invalid_coordinate_metadata",
            "reason": "Coordinate metadata is incomplete or invalid for one or more selected IFC files.",
        }
    if not all(math.isfinite(scale) and scale > 0 for scale in scales):
        return {
            "compatible": False,
            "status": "invalid_coordinate_metadata",
            "reason": "Coordinate metadata contains an invalid project length unit.",
        }
    if not all(math.isclose(scales[0], scale, rel_tol=1e-9, abs_tol=1e-12) for scale in scales[1:]):
        return {
            "compatible": False,
            "status": "unit_mismatch",
            "reason": "Selected IFC files use different project length units; cross-file coordinates were not mixed.",
            "unit_scales": scales,
        }
    common_projects = _string_values(metadata[0].get("project_guids"))
    common_sites = _string_values(metadata[0].get("site_guids"))
    common_buildings = {
        item.get("guid") for item in _dict_records(metadata[0].get("buildings"))
        if item.get("guid")
    }
    for item in metadata[1:]:
        common_projects &= _string_values(item.get("project_guids"))
        common_sites &= _string_values(item.get("site_guids"))
        common_buildings &= {
            building.get("guid")
            for building in _dict_records(item.get("buildings"))
            if building.get("guid")
        }
    context_groups = [_matrix_group(item, "contexts") for item in metadata]
    if any(
        any(not _matrix_close(group[0], matrix) for matrix in group[1:])
        for group in context_groups if group
    ):
        return {
            "compatible": False,
            "status": "ambiguous_coordinate_frame",
            "reason": "One or more selected IFC files contain conflicting model world-coordinate transforms.",
        }
    if not _groups_share_matrix(context_groups, translation_tolerance=_FRAME_ABS_TOLERANCE):
        return {
            "compatible": False,
            "status": "coordinate_frame_mismatch",
            "reason": "Selected IFC files use different or invalid world-coordinate transforms.",
        }
    if not _directions_compatible(metadata):
        return {
            "compatible": False,
            "status": "true_north_mismatch",
            "reason": "Selected IFC files declare conflicting true-north directions.",
        }

    map_groups = [_canonical_maps(item) for item in metadata]
    known_maps = [group for group in map_groups if group]
    common_maps = _shared_values(known_maps) if len(known_maps) == len(metadata) else set()
    if len(known_maps) > 1 and not _shared_values(known_maps):
        return {
            "compatible": False,
            "status": "map_conversion_mismatch",
            "reason": "Selected IFC files declare conflicting map conversions.",
        }

    georef_groups = [_canonical_georefs(item) for item in metadata]
    known_georefs = [group for group in georef_groups if group]
    common_georef = _shared_values(known_georefs) if len(known_georefs) == len(metadata) else set()
    georeference_conflict = len(known_georefs) > 1 and not _shared_values(known_georefs)

    # Project/site object placements are useful cross-export evidence. Missing
    # placements are treated as unknown, but conflicting known placements fail
    # closed. A 2 cm tolerance covers harmless exporter rounding (for example a
    # 5 mm offset) without concealing a meaningful federation transform.
    for source in ("sites", "buildings"):
        placement_groups = [_matrix_group(item, source) for item in metadata]
        known_placements = [group for group in placement_groups if group]
        if len(known_placements) > 1 and not _groups_share_matrix(
            known_placements,
            translation_tolerance=_PLACEMENT_TRANSLATION_TOLERANCE_METRES,
        ):
            return {
                "compatible": False,
                "status": "spatial_placement_mismatch",
                "reason": f"Selected IFC files declare conflicting {source[:-1]} placements.",
            }

    project_labels = _shared_values([_entity_labels(item, "projects") for item in metadata])
    building_labels = _shared_values([_entity_labels(item, "buildings") for item in metadata])
    if common_projects:
        basis = "shared_project_guid"
    elif common_sites:
        basis = "shared_site_guid"
    elif common_buildings:
        basis = "shared_building_guid"
    elif common_maps:
        # An identical explicit map conversion, together with the already
        # verified units/world context/spatial placements above, is strong
        # deterministic federation evidence even when discipline exporters
        # regenerate IfcProject/IfcSite GUIDs.
        basis = "shared_map_conversion"
    elif project_labels and building_labels:
        basis = "shared_project_and_building_identity"
    else:
        basis = None
    if basis:
        result = {
            "compatible": True,
            "status": "verified",
            "basis": basis,
            "reason": "Selected models share units, a compatible world frame, and a verifiable building identity.",
        }
        warnings = []
        if georeference_conflict:
            warnings.append(
                "Site latitude/longitude metadata differs between files; local world transforms were used for federation."
            )
        elif known_georefs and not common_georef:
            warnings.append(
                "Site latitude/longitude metadata is absent from one or more files; local world transforms were used."
            )
        if common_maps:
            result["coordinate_reference"] = "shared_map_conversion"
        if common_sites:
            result["shared_site_guid"] = True
        if warnings:
            result["warnings"] = warnings
        return result
    return {
        "compatible": False,
        "status": "unverified_alignment",
        "reason": (
            "The selected IFC files do not expose a shared project/site/georeference and matching world context. "
            "Cross-file clash detection was stopped to avoid false results. Federate/align the models in the authoring tool first."
        ),
    }
