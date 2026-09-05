"""Extract material and quantity evidence for graph-retained IFC elements."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable

import ifcopenshell
import ifcopenshell.util.element as element_util
import ifcopenshell.util.unit as unit_util

from .config import (
    AREA_BOUNDS_TYPES,
    BOUNDS_UNIT_CONTRACT,
    EXTRACTOR_VERSION,
    LENGTH_BOUNDS_TYPES,
    VOLUME_BOUNDS_TYPES,
)
from .models import ElementEvidence, MaterialUse, QuantityEvidence
from .normalization import material_category, normalize_material_name
from .units import CANONICAL_UNITS, UnitNormalizationError, normalize_ifc_value, quantity_type_from_ifc

_QUANTITY_VALUE_ATTRIBUTE = {
    "IfcQuantityVolume": "VolumeValue",
    "IfcQuantityArea": "AreaValue",
    "IfcQuantityLength": "LengthValue",
    "IfcQuantityWeight": "WeightValue",
}


def _entity_type(entity: Any) -> str:
    try:
        return str(entity.is_a())
    except Exception:
        return type(entity).__name__


def _is_a(entity: Any, ifc_type: str) -> bool:
    try:
        return bool(entity.is_a(ifc_type))
    except TypeError:
        return _entity_type(entity) == ifc_type
    except Exception:
        return _entity_type(entity) == ifc_type


def _entity_id(entity: Any) -> int | None:
    try:
        return int(entity.id())
    except Exception:
        return None


def _stable_id(*parts: Any) -> str:
    value = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _material_use(
    material: Any,
    *,
    element_id: str,
    association: Any,
    association_level: str,
    component_type: str,
    component_index: int,
    layer_thickness_m: float | None = None,
    fraction: float | None = None,
) -> MaterialUse | None:
    if material is None:
        return None
    raw_name = str(getattr(material, "Name", None) or getattr(material, "Category", None) or "").strip()
    if not raw_name:
        raw_name = f"Unnamed material #{_entity_id(material) or 'unknown'}"
    normalized = normalize_material_name(raw_name)
    return MaterialUse(
        id=_stable_id(
            element_id, _entity_id(association), component_type, component_index,
            _entity_id(material), EXTRACTOR_VERSION,
        ),
        raw_name=raw_name,
        normalized_name=normalized,
        category=material_category(normalized),
        association_type=_entity_type(association),
        association_level=association_level,
        component_type=component_type,
        component_index=component_index,
        layer_thickness_m=layer_thickness_m,
        fraction=fraction,
        material_ifc_id=_entity_id(material),
    )


def _flatten_material_definition(
    definition: Any,
    *,
    element_id: str,
    association: Any,
    association_level: str,
    length_scale_to_m: float,
) -> list[MaterialUse]:
    """Flatten IFC2X3 and IFC4 IfcMaterialSelect structures."""
    if definition is None:
        return []
    kind = _entity_type(definition)
    if kind == "IfcMaterial":
        item = _material_use(
            definition, element_id=element_id, association=association,
            association_level=association_level, component_type="material", component_index=0,
        )
        return [item] if item else []
    if kind == "IfcMaterialLayerSetUsage":
        return _flatten_material_definition(
            getattr(definition, "ForLayerSet", None), element_id=element_id,
            association=association, association_level=association_level,
            length_scale_to_m=length_scale_to_m,
        )
    if kind == "IfcMaterialLayerSet":
        output: list[MaterialUse] = []
        for index, layer in enumerate(getattr(definition, "MaterialLayers", None) or []):
            thickness = getattr(layer, "LayerThickness", None)
            item = _material_use(
                getattr(layer, "Material", None), element_id=element_id,
                association=association, association_level=association_level,
                component_type="layer", component_index=index,
                layer_thickness_m=float(thickness) * length_scale_to_m if thickness is not None else None,
            )
            if item:
                output.append(item)
        return output
    if kind == "IfcMaterialLayer":
        thickness = getattr(definition, "LayerThickness", None)
        item = _material_use(
            getattr(definition, "Material", None), element_id=element_id,
            association=association, association_level=association_level,
            component_type="layer", component_index=0,
            layer_thickness_m=float(thickness) * length_scale_to_m if thickness is not None else None,
        )
        return [item] if item else []
    if kind in {"IfcMaterialProfileSetUsage", "IfcMaterialProfileSetUsageTapering"}:
        return _flatten_material_definition(
            getattr(definition, "ForProfileSet", None), element_id=element_id,
            association=association, association_level=association_level,
            length_scale_to_m=length_scale_to_m,
        )
    if kind == "IfcMaterialProfileSet":
        output = []
        for index, profile in enumerate(getattr(definition, "MaterialProfiles", None) or []):
            item = _material_use(
                getattr(profile, "Material", None), element_id=element_id,
                association=association, association_level=association_level,
                component_type="profile", component_index=index,
            )
            if item:
                output.append(item)
        return output
    if kind == "IfcMaterialProfile":
        item = _material_use(
            getattr(definition, "Material", None), element_id=element_id,
            association=association, association_level=association_level,
            component_type="profile", component_index=0,
        )
        return [item] if item else []
    if kind == "IfcMaterialConstituentSet":
        output = []
        for index, constituent in enumerate(getattr(definition, "MaterialConstituents", None) or []):
            fraction = getattr(constituent, "Fraction", None)
            item = _material_use(
                getattr(constituent, "Material", None), element_id=element_id,
                association=association, association_level=association_level,
                component_type="constituent", component_index=index,
                fraction=float(fraction) if fraction is not None else None,
            )
            if item:
                output.append(item)
        return output
    if kind == "IfcMaterialConstituent":
        fraction = getattr(definition, "Fraction", None)
        item = _material_use(
            getattr(definition, "Material", None), element_id=element_id,
            association=association, association_level=association_level,
            component_type="constituent", component_index=0,
            fraction=float(fraction) if fraction is not None else None,
        )
        return [item] if item else []
    if kind == "IfcMaterialList":
        output = []
        for index, material in enumerate(getattr(definition, "Materials", None) or []):
            item = _material_use(
                material, element_id=element_id, association=association,
                association_level=association_level, component_type="list_item",
                component_index=index,
            )
            if item:
                output.append(item)
        return output
    return []


def extract_material_uses(element: Any, element_id: str, length_scale_to_m: float) -> list[MaterialUse]:
    """Resolve occurrence material associations, falling back to type associations."""
    association_levels: list[tuple[str, Any]] = [("occurrence", element)]
    try:
        type_object = element_util.get_type(element)
    except Exception:
        type_object = None
    if type_object is not None and type_object is not element:
        association_levels.append(("type", type_object))

    for level, owner in association_levels:
        relationships = [
            rel for rel in (getattr(owner, "HasAssociations", None) or [])
            if _is_a(rel, "IfcRelAssociatesMaterial")
        ]
        if not relationships:
            continue
        output: list[MaterialUse] = []
        for association in relationships:
            output.extend(_flatten_material_definition(
                getattr(association, "RelatingMaterial", None), element_id=element_id,
                association=association, association_level=level,
                length_scale_to_m=length_scale_to_m,
            ))
        if output:
            # Occurrence material assignment takes precedence; do not duplicate inherited type uses.
            return list({item.id: item for item in output}.values())
    return []


def _quantity_unit(quantity: Any, model: Any) -> Any:
    if getattr(quantity, "Unit", None) is not None:
        return quantity.Unit
    try:
        return unit_util.get_property_unit(quantity, model)
    except Exception:
        return None


def _walk_quantities(values: Iterable[Any]) -> Iterable[Any]:
    for quantity in values or []:
        if _is_a(quantity, "IfcPhysicalComplexQuantity"):
            yield from _walk_quantities(getattr(quantity, "HasQuantities", None) or [])
        else:
            yield quantity


def _quantity_sets(element: Any) -> list[tuple[str, Any]]:
    output: list[tuple[str, Any]] = []
    for rel in getattr(element, "IsDefinedBy", None) or []:
        if not _is_a(rel, "IfcRelDefinesByProperties"):
            continue
        definition = getattr(rel, "RelatingPropertyDefinition", None)
        if definition is not None and _is_a(definition, "IfcElementQuantity"):
            output.append(("occurrence", definition))
    try:
        type_object = element_util.get_type(element)
    except Exception:
        type_object = None
    for definition in getattr(type_object, "HasPropertySets", None) or []:
        if _is_a(definition, "IfcElementQuantity"):
            output.append(("type", definition))
    return output


def extract_explicit_quantities(element: Any, element_id: str, model: Any) -> list[QuantityEvidence]:
    output: list[QuantityEvidence] = []
    seen_entity_ids: set[int] = set()
    for level, quantity_set in _quantity_sets(element):
        for quantity in _walk_quantities(getattr(quantity_set, "Quantities", None) or []):
            ifc_type = _entity_type(quantity)
            quantity_type = quantity_type_from_ifc(ifc_type)
            attribute = _QUANTITY_VALUE_ATTRIBUTE.get(ifc_type)
            if not quantity_type or not attribute:
                continue
            name = str(getattr(quantity, "Name", "") or "")
            quantity_entity_id = _entity_id(quantity)
            if quantity_entity_id is not None and quantity_entity_id in seen_entity_ids:
                continue
            if quantity_entity_id is not None:
                seen_entity_ids.add(quantity_entity_id)
            raw_value = getattr(quantity, attribute, None)
            unit = _quantity_unit(quantity, model)
            evidence_id = _stable_id(element_id, _entity_id(quantity_set), quantity_entity_id, EXTRACTOR_VERSION)
            try:
                normalized, normalized_unit, raw_unit = normalize_ifc_value(
                    float(raw_value), unit, quantity_type
                )
                error = None
            except (TypeError, ValueError, UnitNormalizationError) as exc:
                normalized, normalized_unit = None, CANONICAL_UNITS[quantity_type]
                raw_unit, error = "unknown", str(exc)
            output.append(QuantityEvidence(
                id=evidence_id,
                quantity_value=float(raw_value) if raw_value is not None else None,
                quantity_type=quantity_type,
                quantity_unit=raw_unit,
                quantity_source="ifc_explicit",
                normalized_value=normalized,
                normalized_unit=normalized_unit,
                quantity_name=name,
                quantity_set_name=str(getattr(quantity_set, "Name", "") or ""),
                association_level=level,
                quality="authored",
                error=error,
            ))
    return output


def derive_bound_quantities(
    graph_element: dict[str, Any],
    *,
    length_scale_to_m: float,
    existing_types: set[str],
) -> list[QuantityEvidence]:
    """Create clearly marked envelope estimates only for missing dimensions."""
    ifc_type = str(graph_element.get("ifc_type") or graph_element.get("ifcType") or "")
    element_id = str(graph_element.get("element_id") or graph_element.get("id") or "")
    try:
        dx = abs(float(graph_element["max_x"]) - float(graph_element["min_x"])) * length_scale_to_m
        dy = abs(float(graph_element["max_y"]) - float(graph_element["min_y"])) * length_scale_to_m
        dz = abs(float(graph_element["max_z"]) - float(graph_element["min_z"])) * length_scale_to_m
    except (KeyError, TypeError, ValueError):
        return []
    if not all(math.isfinite(item) and item > 0 for item in (dx, dy, dz)):
        return []
    output = []
    definitions = []
    if "volume" not in existing_types and ifc_type in VOLUME_BOUNDS_TYPES:
        definitions.append(("volume", dx * dy * dz, "aabb_envelope_volume"))
    if "area" not in existing_types and ifc_type in AREA_BOUNDS_TYPES:
        definitions.append(("area", max(dx * dy, dx * dz, dy * dz), "aabb_max_face_area"))
    if "length" not in existing_types and ifc_type in LENGTH_BOUNDS_TYPES:
        definitions.append(("length", max(dx, dy, dz), "aabb_max_extent"))
    for quantity_type, value, method in definitions:
        output.append(QuantityEvidence(
            id=_stable_id(element_id, method, EXTRACTOR_VERSION),
            quantity_value=value,
            quantity_type=quantity_type,
            quantity_unit=CANONICAL_UNITS[quantity_type],
            quantity_source="geometry_derived",
            normalized_value=value,
            normalized_unit=CANONICAL_UNITS[quantity_type],
            quantity_name=f"Derived {quantity_type}",
            derivation_method=f"{method}:{BOUNDS_UNIT_CONTRACT}",
            quality="low",
        ))
    return output


def validate_explicit_quantity_plausibility(
    quantities: list[QuantityEvidence],
    graph_element: dict[str, Any],
    *,
    length_scale_to_m: float,
) -> None:
    """Reject gross unit/export inconsistencies without rewriting IFC evidence.

    This intentionally uses a generous 100x envelope threshold. It catches
    exponent-scale mistakes (for example mm3 values declared as m3) while not
    pretending that an AABB is an exact validation solid.
    """
    try:
        dx = abs(float(graph_element["max_x"]) - float(graph_element["min_x"])) * length_scale_to_m
        dy = abs(float(graph_element["max_y"]) - float(graph_element["min_y"])) * length_scale_to_m
        dz = abs(float(graph_element["max_z"]) - float(graph_element["min_z"])) * length_scale_to_m
    except (KeyError, TypeError, ValueError):
        return
    if not all(math.isfinite(item) and item > 0 for item in (dx, dy, dz)):
        return
    limits = {
        "volume": dx * dy * dz,
        "area": 2.0 * (dx * dy + dx * dz + dy * dz),
        "length": 4.0 * (dx + dy + dz),
    }
    for quantity in quantities:
        limit = limits.get(quantity.quantity_type)
        if limit is None or quantity.normalized_value is None:
            continue
        if float(quantity.normalized_value) > max(limit * 100.0, 1e-12):
            quantity.error = (
                "Explicit IFC quantity is implausible relative to the element bounds; "
                "a project/local unit inconsistency is suspected."
            )


def extract_element_evidence(
    model: Any,
    element: Any,
    graph_element: dict[str, Any],
    file_record: dict[str, Any],
    *,
    allow_geometry_derived: bool = False,
) -> ElementEvidence:
    scale = float(file_record.get("coordinate_system", {}).get("unit_scale_to_metre", 1.0))
    element_id = str(graph_element.get("element_id") or graph_element.get("id"))
    explicit = extract_explicit_quantities(element, element_id, model)
    validate_explicit_quantity_plausibility(explicit, graph_element, length_scale_to_m=1.0)
    quantities = list(explicit)
    if allow_geometry_derived:
        quantities.extend(derive_bound_quantities(
            graph_element,
            length_scale_to_m=1.0,
            existing_types={
                item.quantity_type for item in explicit
                if item.normalized_value is not None and not item.error
            },
        ))
    if not quantities:
        quantities.append(QuantityEvidence(
            id=_stable_id(element_id, "unavailable", EXTRACTOR_VERSION),
            quantity_value=None,
            quantity_type="unknown",
            quantity_unit="unknown",
            quantity_source="unavailable",
            quality="missing",
            error="No supported explicit or derived quantity is available.",
        ))
    return ElementEvidence(
        element_id=element_id,
        ifc_guid=str(graph_element.get("ifc_guid") or graph_element.get("ifcGuid") or getattr(element, "GlobalId", "")),
        ifc_type=str(graph_element.get("ifc_type") or graph_element.get("ifcType") or _entity_type(element)),
        name=str(graph_element.get("name") or getattr(element, "Name", "") or ""),
        storey_name=str(graph_element.get("storey_name") or graph_element.get("storeyName") or ""),
        project_id=str(file_record["project_id"]),
        source_file_id=str(file_record["file_id"]),
        source_ifc_file=str(file_record["filename"]),
        discipline=str(file_record.get("discipline") or "unspecified"),
        materials=extract_material_uses(element, element_id, scale),
        quantities=quantities,
    )


def extract_file_evidence(
    ifc_path: str,
    graph_elements: list[dict[str, Any]],
    file_record: dict[str, Any],
    *,
    allow_geometry_derived: bool = False,
) -> list[ElementEvidence]:
    model = ifcopenshell.open(ifc_path)
    output: list[ElementEvidence] = []
    for graph_element in graph_elements:
        guid = str(graph_element.get("ifc_guid") or graph_element.get("ifcGuid") or "")
        try:
            element = model.by_guid(guid)
        except Exception:
            element_id = str(graph_element.get("element_id") or graph_element.get("id"))
            output.append(ElementEvidence(
                element_id=element_id, ifc_guid=guid,
                ifc_type=str(graph_element.get("ifc_type") or graph_element.get("ifcType") or "unknown"),
                name=str(graph_element.get("name") or ""),
                storey_name=str(graph_element.get("storey_name") or graph_element.get("storeyName") or ""),
                project_id=str(file_record["project_id"]),
                source_file_id=str(file_record["file_id"]),
                source_ifc_file=str(file_record["filename"]),
                discipline=str(file_record.get("discipline") or "unspecified"),
                quantities=[QuantityEvidence(
                    id=_stable_id(element_id, "unavailable", EXTRACTOR_VERSION),
                    quantity_value=None, quantity_type="unknown", quantity_unit="unknown",
                    quantity_source="unavailable", quality="missing",
                    error="The graph element GUID could not be resolved in the registered IFC file.",
                )],
            ))
            continue
        output.append(extract_element_evidence(
            model, element, graph_element, file_record,
            allow_geometry_derived=allow_geometry_derived,
        ))
    return output
