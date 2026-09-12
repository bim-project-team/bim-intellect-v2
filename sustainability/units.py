"""Reusable IFC and textual unit normalization with dimensional safety."""

from __future__ import annotations

import math
import re
from typing import Any

CANONICAL_UNITS = {"mass": "kg", "volume": "m3", "area": "m2", "length": "m"}
_POWER = {"length": 1, "area": 2, "volume": 3, "mass": 1}
_PREFIX = {
    None: 1.0, "": 1.0, "MILLI": 1e-3, "CENTI": 1e-2,
    "DECI": 1e-1, "KILO": 1e3, "MICRO": 1e-6,
}
_TEXT_SCALES = {
    ("mass", "kg"): 1.0, ("mass", "kilogram"): 1.0,
    ("mass", "g"): 1e-3, ("mass", "gram"): 1e-3,
    ("mass", "lb"): 0.45359237, ("mass", "pound"): 0.45359237,
    ("length", "m"): 1.0, ("length", "metre"): 1.0, ("length", "meter"): 1.0,
    ("length", "mm"): 1e-3, ("length", "millimetre"): 1e-3,
    ("length", "cm"): 1e-2, ("length", "ft"): 0.3048, ("length", "foot"): 0.3048,
    ("area", "m2"): 1.0, ("area", "m^2"): 1.0, ("area", "square metre"): 1.0,
    ("area", "mm2"): 1e-6, ("area", "mm^2"): 1e-6,
    ("area", "ft2"): 0.09290304, ("area", "ft^2"): 0.09290304,
    ("area", "square foot"): 0.09290304,
    ("volume", "m3"): 1.0, ("volume", "m^3"): 1.0, ("volume", "cubic metre"): 1.0,
    ("volume", "mm3"): 1e-9, ("volume", "mm^3"): 1e-9,
    ("volume", "ft3"): 0.028316846592, ("volume", "ft^3"): 0.028316846592,
    ("volume", "cubic foot"): 0.028316846592,
}


class UnitNormalizationError(ValueError):
    pass


def quantity_type_from_ifc(ifc_type: str) -> str | None:
    return {
        "IfcQuantityWeight": "mass",
        "IfcQuantityVolume": "volume",
        "IfcQuantityArea": "area",
        "IfcQuantityLength": "length",
    }.get(ifc_type)


def factor_quantity_type(factor_unit: str) -> str:
    normalized = re.sub(r"\s+", "", factor_unit or "").casefold().replace("³", "3").replace("²", "2")
    mapping = {
        "kgco2e/kg": "mass", "kgco₂e/kg": "mass",
        "kgco2e/m3": "volume", "kgco₂e/m3": "volume", "kgco2e/m^3": "volume",
        "kgco2e/m2": "area", "kgco₂e/m2": "area", "kgco2e/m^2": "area",
    }
    if normalized not in mapping:
        raise UnitNormalizationError(f"Unsupported carbon-factor unit: {factor_unit!r}")
    return mapping[normalized]


def normalize_text_quantity(value: float, unit: str, quantity_type: str) -> tuple[float, str]:
    key = re.sub(r"\s+", " ", (unit or "").strip().casefold())
    scale = _TEXT_SCALES.get((quantity_type, key))
    if scale is None:
        raise UnitNormalizationError(f"Unsupported {quantity_type} unit: {unit!r}")
    return float(value) * scale, CANONICAL_UNITS[quantity_type]


def _wrapped_number(value: Any) -> float:
    return float(getattr(value, "wrappedValue", value))


def ifc_unit_scale(unit: Any, quantity_type: str) -> float:
    """Return the multiplier from an IfcNamedUnit to the canonical SI base."""
    if unit is None:
        raise UnitNormalizationError("IFC unit is unavailable")
    kind = unit.is_a() if hasattr(unit, "is_a") else type(unit).__name__
    if kind in {"IfcConversionBasedUnit", "IfcConversionBasedUnitWithOffset"}:
        conversion = unit.ConversionFactor
        return _wrapped_number(conversion.ValueComponent) * ifc_unit_scale(
            conversion.UnitComponent, quantity_type
        )
    if kind == "IfcSIUnit":
        name = str(getattr(unit, "Name", "") or "").upper()
        prefix = str(getattr(unit, "Prefix", "") or "").upper() or None
        multiplier = _PREFIX.get(prefix)
        if multiplier is None:
            raise UnitNormalizationError(f"Unsupported IFC SI prefix: {prefix}")
        if quantity_type == "mass":
            if name != "GRAM":
                raise UnitNormalizationError(f"Unsupported IFC mass unit: {name}")
            return multiplier * 1e-3
        expected = {"length": "METRE", "area": "SQUARE_METRE", "volume": "CUBIC_METRE"}[quantity_type]
        if name != expected:
            raise UnitNormalizationError(f"Unexpected IFC {quantity_type} unit: {name}")
        return multiplier ** _POWER[quantity_type]
    # Named conversion units from lightweight fixtures or external callers.
    name = str(getattr(unit, "Name", "") or "").replace("_", " ")
    return normalize_text_quantity(1.0, name, quantity_type)[0]


def ifc_unit_label(unit: Any, quantity_type: str) -> str:
    if unit is None:
        return "unknown"
    name = str(getattr(unit, "Name", "") or "").replace("_", " ").lower()
    prefix = str(getattr(unit, "Prefix", "") or "").lower()
    return f"{prefix}{name}" if prefix else (name or CANONICAL_UNITS.get(quantity_type, "unknown"))


def normalize_ifc_value(value: float, unit: Any, quantity_type: str) -> tuple[float, str, str]:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise UnitNormalizationError(f"Invalid {quantity_type} quantity: {value!r}")
    return numeric * ifc_unit_scale(unit, quantity_type), CANONICAL_UNITS[quantity_type], ifc_unit_label(unit, quantity_type)


def dimensions_compatible(quantity_type: str, factor_unit: str) -> bool:
    try:
        return factor_quantity_type(factor_unit) == quantity_type
    except UnitNormalizationError:
        return False
