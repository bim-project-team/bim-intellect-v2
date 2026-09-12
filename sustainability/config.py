"""Configuration and versioned policies for sustainability analysis."""

from __future__ import annotations

import os
from pathlib import Path

METHODOLOGY_VERSION = "1.0"
EXTRACTOR_VERSION = "1.0"
NORMALIZATION_VERSION = "1.0"

CARBON_FACTORS_PATH = Path(
    os.getenv("SUSTAINABILITY_CARBON_FACTORS", "dataset/sustainability/carbon_factors.csv")
)

# extract_graph.py uses IfcOpenShell's default CONVERT_BACK_UNITS=False. Its
# tessellated world-coordinate vertices—and therefore stored AABBs—are metres.
# This is intentionally separate from project units used by material layer
# thicknesses and IFC quantities.
BOUNDS_UNIT_CONTRACT = "ifcopenshell_si_metres"

SPATIAL_IFC_TYPES = {
    "IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey",
}

# AABB estimates are intentionally conservative in scope. They are envelopes,
# not exact material take-offs, and always retain geometry_derived provenance.
VOLUME_BOUNDS_TYPES = {
    "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcBeam", "IfcColumn",
}
AREA_BOUNDS_TYPES = {
    "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcCovering", "IfcRoof",
}
LENGTH_BOUNDS_TYPES = {
    "IfcBeam", "IfcColumn", "IfcFlowSegment",
}

QUANTITY_NAME_PRIORITY = {
    "mass": ("NetWeight", "Weight", "GrossWeight"),
    "volume": ("NetVolume", "Volume", "GrossVolume"),
    "area": (
        "NetArea", "Area", "NetSurfaceArea", "NetSideArea", "GrossArea",
        "GrossSurfaceArea", "GrossFootprintArea", "GSA BIM Area",
    ),
    "length": ("Length", "Height", "Width", "Depth", "Perimeter"),
}

NEO4J_BATCH_SIZE = int(os.getenv("SUSTAINABILITY_BATCH_SIZE", "500"))
