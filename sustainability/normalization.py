"""Deterministic material-name normalization; raw IFC names remain untouched."""

from __future__ import annotations

import re
import unicodedata

from .config import NORMALIZATION_VERSION

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("concrete", ("concrete", "cement", "بتن", "سیمان")),
    ("steel", ("steel", "stainless", "فولاد", "آهن")),
    ("aluminium", ("aluminium", "aluminum", "آلومینیوم")),
    ("timber", ("timber", "wood", "plywood", "چوب")),
    ("glass", ("glass", "glazing", "شیشه")),
    ("gypsum", ("gypsum", "plasterboard", "drywall", "گچ")),
    ("insulation", ("insulation", "mineral wool", "rockwool", "عایق")),
    ("masonry", ("brick", "masonry", "block", "آجر", "بلوک")),
)


def normalize_material_name(value: str | None) -> str:
    """Return a stable lookup key without modifying the authored value."""
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.replace("ي", "ی").replace("ك", "ک").replace("&", " and ")
    text = re.sub(r"[_/\\|:;,+]+", " ", text)
    text = re.sub(r"[-‐‑‒–—−]+", " ", text)
    text = re.sub(r"[^\w\s؀-ۿ]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def material_category(normalized_name: str) -> str:
    for category, terms in _CATEGORY_RULES:
        if any(term in normalized_name for term in terms):
            return category
    return "other"


def normalization_metadata(value: str | None) -> dict[str, str]:
    normalized = normalize_material_name(value)
    return {
        "raw_name": value or "",
        "normalized_name": normalized,
        "category": material_category(normalized),
        "normalization_version": NORMALIZATION_VERSION,
    }
