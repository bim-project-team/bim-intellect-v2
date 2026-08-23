"""
extract_graph.py
-----------------
Extracts semantic building elements (NOT raw geometry entities) from an IFC
file into two CSVs shaped for a fast Neo4j `LOAD CSV` bulk import:

    nodes.csv  - one row per building element (wall, door, space, etc.)
                 plus the spatial hierarchy (Project/Site/Building/Storey)
    edges.csv  - one row per relationship (containment, aggregation,
                 space-boundary, port-connection)

Why CSV + LOAD CSV instead of the Python driver in a loop:
On a file this size (2.7M raw entities), inserting node-by-node via the
Neo4j Python driver is orders of magnitude slower than a single
`LOAD CSV ... CREATE` pass. Generate the CSVs here, then bulk-import via
bim_graph.load_to_neo4j.

Install once:
    pip install ifcopenshell

Run directly:
    python extract_graph.py --ifc dataset/ifc/model.ifc --storey "Level 1" --type IfcWall

Called from the web app via:
    from extract_graph import run_extraction
    node_rows, edge_rows = run_extraction(ifc_path, nodes_csv=..., edges_csv=...,
                                           storey_filter=[...], type_filter=[...])
"""

import csv
import sys
import multiprocessing
from pathlib import Path
import ifcopenshell
import ifcopenshell.geom as geom
import ifcopenshell.util.element as Element
from tqdm import tqdm

IFC_PATH = "dataset/210_King_Merged.ifc"
NODES_CSV = "nodes.csv"
EDGES_CSV = "edges.csv"

# --- Filters ---------------------------------------------------------------
# Pass storey_filter / type_filter as arguments to run_extraction() rather
# than module globals - this keeps the module safe to import and call
# concurrently (e.g. from the FastAPI /ingest endpoint) without one
# request's filter leaking into another's.

# Only these types become semantic-element graph nodes. Extend as needed
# once you've decided which disciplines (arch / structural / MEP) are in
# scope. NOTE: this is intentionally narrower than the full set of IFC
# classes in a file (see extract_sotreys_type.py, which lists every class
# present for the UI's "IFC Type Filter" dropdown) - spatial-structure
# classes (IfcProject/IfcSite/IfcBuilding/IfcBuildingStorey) are handled
# separately below and relationship/property classes are never valid graph
# nodes here, so both are excluded even if a caller passes them in.
#
# NOTE: model.by_type() is subtype-inclusive, so listing both a parent and
# its own subtype here (e.g. "IfcWall" + "IfcWallStandardCase", the latter
# being an IFC2X3-only subtype of the former) makes every matching element
# get filtered - and counted as a candidate - twice under two different
# entity_type passes. write_csvs() dedupes by id so this can't produce bad
# output, but it's wasted filtering work, so only the top-level type is
# listed here.
SEMANTIC_TYPES = [
    "IfcWall", "IfcDoor", "IfcWindow", "IfcColumn",
    "IfcBeam", "IfcSlab", "IfcStair", "IfcStairFlight", "IfcTransportElement",
    "IfcRailing", "IfcRoof", "IfcCurtainWall", "IfcCovering", "IfcSpace",
    "IfcBuildingElementProxy", "IfcFlowSegment", "IfcFlowFitting",
    "IfcFlowTerminal", "IfcFlowController",
]

# Always written as spatial-hierarchy nodes by extract(), independent of
# type_filter. Kept out of the semantic-element pass below so a caller
# passing these in type_filter (e.g. a UI "Select all" that includes every
# class found in the file) can never create a second, duplicate node row
# for the same GlobalId.
SPATIAL_TYPES = ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey")

geom_settings = geom.settings()
geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
# Perf tweaks: these skip work inside each create_shape() call without
# changing the lazy, per-element/filtered call pattern below.
# Bounding boxes don't need door/window cutouts subtracted from
# walls/slabs - the outer envelope is unaffected in normal cases - so this
# removes the expensive boolean CSG step.
geom_settings.set(geom_settings.DISABLE_OPENING_SUBTRACTIONS, True)
# Skip material resolution - not needed for bbox extraction.
geom_settings.set(geom_settings.APPLY_DEFAULT_MATERIALS, False)


def extract_geometry_for(model, elements):
    """
    Bulk-extract bounding boxes for exactly the given `elements` list (NOT
    the whole model), using IfcOpenShell's multi-threaded iterator
    restricted via `include=`. This gives us the iterator's parallelism
    without paying for geometry across the entire merged multi-building
    file when only a storey/type-filtered subset was asked for.

    Returns dict mapping GlobalId -> (min_x, min_y, min_z, max_x, max_y, max_z).
    Elements with no representable geometry are simply absent from the dict.
    """
    if not elements:
        return {}

    iterator = geom.iterator(
        geom_settings, model, multiprocessing.cpu_count(), include=elements
    )
    bbox_map = {}
    if iterator.initialize():
        pbar = tqdm(total=len(elements), desc="Extracting geometry (parallel, scoped)",
                    unit="elem", file=sys.stderr)
        while True:
            shape = iterator.get()
            verts = shape.geometry.verts
            if verts:
                xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
                bbox_map[shape.guid] = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
            pbar.update(1)
            if not iterator.next():
                break
        pbar.close()
    return bbox_map


def storey_of(element):
    """Walk containment relationships to find the owning IfcBuildingStorey."""
    container = Element.get_container(element)
    if container is None:
        return None
    return container.GlobalId, container.Name


def _safe_by_type(model, entity_type):
    """
    model.by_type() raises RuntimeError on an unrecognized/misspelled type
    name. That happens in practice when a type_filter was generated
    against a different IFC file than the one currently loaded (e.g. a
    stale ifc_types.csv). Skip it instead of failing the whole request.
    """
    try:
        return model.by_type(entity_type)
    except RuntimeError:
        print(f"  Skipping unknown IFC type: {entity_type}", file=sys.stderr)
        return []


def _node_id(guid, project_id=None, source_file_id=None):
    """Use a composite graph identity for federated models; retain the IFC GUID separately."""
    if project_id and source_file_id:
        return f"{project_id}::{source_file_id}::{guid}"
    return guid


def _normalize_optional_filter(values):
    """Treat omitted and empty UI selections identically: both mean all."""
    if not values:
        return None
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return normalized or None


def extract(
    model,
    storey_filter=None,
    type_filter=None,
    *,
    project_id=None,
    source_ifc_file=None,
    source_file_id=None,
    discipline="unspecified",
    coordinate_system_id=None,
):
    # The multiselect sends no values until the user chooses a subset. An
    # empty selection means "All", not "None". Keep API/CLI behavior equal.
    storey_filter = _normalize_optional_filter(storey_filter)
    type_filter = _normalize_optional_filter(type_filter)
    node_rows = []
    edge_rows = []

    # --- spatial hierarchy nodes + edges (Project/Site/Building/Storey) ---
    # Always included, independent of type_filter/storey_filter - the
    # pipeline needs the storey nodes to exist for CONTAINS edges to
    # resolve, even when the caller only asked for e.g. IfcDoor elements.
    for rel in model.by_type("IfcRelAggregates"):
        parent = rel.RelatingObject
        for child in rel.RelatedObjects:
            if parent.is_a("IfcSpatialStructureElement") or parent.is_a("IfcProject"):
                edge_rows.append([
                    _node_id(parent.GlobalId, project_id, source_file_id),
                    _node_id(child.GlobalId, project_id, source_file_id),
                    "AGGREGATES",
                ])

    spatial_ids = set()
    for entity_type in SPATIAL_TYPES:
        for e in model.by_type(entity_type):
            spatial_ids.add(e.GlobalId)
            node_rows.append({
                "id": _node_id(e.GlobalId, project_id, source_file_id),
                "ifc_guid": e.GlobalId, "type": e.is_a(), "name": e.Name or "",
                "storey_id": "", "storey_name": "",
                "min_x": "", "min_y": "", "min_z": "",
                "max_x": "", "max_y": "", "max_z": "",
                "source_ifc_file": source_ifc_file or "",
                "source_file_id": source_file_id or "",
                "discipline": discipline or "unspecified",
                "project_id": project_id or "",
                "coordinate_system_id": coordinate_system_id or "",
            })

    # --- semantic building element nodes ---
    # Whitelist against SEMANTIC_TYPES rather than trusting type_filter
    # as-is. The UI's Type dropdown is populated from ifc_types.csv, which
    # lists every IFC class present in the file (extract_sotreys_type.py
    # counts ALL entities, not just building elements) - so a "Select all"
    # click can hand us geometry/representation classes like IfcPolyLoop
    # or IfcCartesianPoint that aren't even IfcRoot subtypes and have no
    # GlobalId. Silently drop anything outside SEMANTIC_TYPES instead of
    # crashing the request.
    requested_types = type_filter if type_filter else SEMANTIC_TYPES
    types_to_extract = [t for t in requested_types if t in SEMANTIC_TYPES]
    ignored_types = sorted(set(requested_types) - set(types_to_extract))
    if ignored_types:
        print(f"Ignoring {len(ignored_types)} non-semantic/unsupported type(s) "
              f"from type_filter: {ignored_types}", file=sys.stderr)

    # Pass 1: figure out which elements survive the storey/type filter.
    # This is cheap - no geometry touched yet - so it's fine to do in a
    # plain Python loop even across a huge multi-building file.
    candidates = []  # list of (element, storey) tuples
    for entity_type in types_to_extract:
        elements = _safe_by_type(model, entity_type)
        for e in tqdm(elements, desc=f"Filtering {entity_type}", unit="elem", file=sys.stderr):
            if e.GlobalId in spatial_ids:
                continue
            storey = storey_of(e)
            if storey_filter:
                storey_name = storey[1] if storey else None
                if storey_name not in storey_filter:
                    continue
            candidates.append((e, storey))

    # Pass 2: compute bounding boxes ONLY for the filtered candidates, in
    # one parallel batch - the iterator must never run over the whole
    # model regardless of filters.
    bbox_map = extract_geometry_for(model, [e for e, _ in candidates])
    print(f"Geometry extracted for {len(bbox_map)} of {len(candidates)} filtered elements.",
          file=sys.stderr, flush=True)

    included_ids = set(spatial_ids)  # track which element ids survive
                                      # filtering, so downstream edge loops
                                      # (space boundaries, ports) only
                                      # reference elements we kept
    for e, storey in candidates:
        bbox = bbox_map.get(e.GlobalId)
        included_ids.add(e.GlobalId)
        node_rows.append({
            "id": _node_id(e.GlobalId, project_id, source_file_id),
            "ifc_guid": e.GlobalId, "type": e.is_a(), "name": e.Name or "",
            "storey_id": storey[0] if storey else "",
            "storey_name": storey[1] if storey else "",
            "min_x": bbox[0] if bbox else "", "min_y": bbox[1] if bbox else "",
            "min_z": bbox[2] if bbox else "", "max_x": bbox[3] if bbox else "",
            "max_y": bbox[4] if bbox else "", "max_z": bbox[5] if bbox else "",
            "source_ifc_file": source_ifc_file or "",
            "source_file_id": source_file_id or "",
            "discipline": discipline or "unspecified",
            "project_id": project_id or "",
            "coordinate_system_id": coordinate_system_id or "",
        })
        if storey:
            edge_rows.append([
                _node_id(storey[0], project_id, source_file_id),
                _node_id(e.GlobalId, project_id, source_file_id),
                "CONTAINS",
            ])

    # --- space boundary edges (space <-> element), useful for clearance rules ---
    # Only kept when both ends survived the type/storey filters above, so
    # edges.csv never references a node id that isn't in nodes.csv.
    for rel in model.by_type("IfcRelSpaceBoundary"):
        space = rel.RelatingSpace
        element = rel.RelatedBuildingElement
        if space and element:
            if space.GlobalId in included_ids and element.GlobalId in included_ids:
                edge_rows.append([
                    _node_id(space.GlobalId, project_id, source_file_id),
                    _node_id(element.GlobalId, project_id, source_file_id),
                    "BOUNDS",
                ])

    # --- MEP port connections (useful if MEP is in scope for clash checks) ---
    for rel in model.by_type("IfcRelConnectsPortToElement"):
        port = rel.RelatingPort
        element = rel.RelatedElement
        if port and element:
            if element.GlobalId in included_ids:
                edge_rows.append([
                    _node_id(port.GlobalId, project_id, source_file_id),
                    _node_id(element.GlobalId, project_id, source_file_id),
                    "PORT_OF",
                ])

    return node_rows, edge_rows


def write_csvs(node_rows, edge_rows, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV):
    # Dedupe nodes by id, keeping the first occurrence. The spatial-hierarchy
    # loop runs before the semantic-element loop, so a legitimate storey
    # node always wins over any accidental re-processing of the same id.
    seen_ids = set()
    deduped_nodes = []
    for row in node_rows:
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        deduped_nodes.append(row)
    dropped_dupes = len(node_rows) - len(deduped_nodes)
    if dropped_dupes:
        print(f"Dropped {dropped_dupes} duplicate node id(s).", file=sys.stderr)
    node_rows = deduped_nodes

    # Safety net: drop any edge whose source or target id isn't actually
    # present as a node. This matters because the AGGREGATES loop walks the
    # WHOLE file's Project/Site/Building/Storey tree unfiltered, while the
    # semantic-element loop respects storey_filter/type_filter - so without
    # this check, edges.csv can reference elements that were correctly
    # excluded as nodes, and Neo4j's MATCH would just silently skip loading
    # them (no error).
    node_ids = {row["id"] for row in node_rows}
    before = len(edge_rows)
    edge_rows = [e for e in edge_rows if e[0] in node_ids and e[1] in node_ids]
    dropped_edges = before - len(edge_rows)
    if dropped_edges:
        print(f"Dropped {dropped_edges} edge(s) referencing ids outside the extracted node set "
              f"(expected when storey_filter/type_filter narrows the extraction).", file=sys.stderr)

    with open(nodes_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "ifc_guid", "type", "name", "storey_id", "storey_name",
            "min_x", "min_y", "min_z", "max_x", "max_y", "max_z",
            "source_ifc_file", "source_file_id", "discipline", "project_id",
            "coordinate_system_id",
        ])
        writer.writeheader()
        writer.writerows(node_rows)

    with open(edges_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source_id", "target_id", "rel_type"])
        writer.writerows(edge_rows)

    print(f"Wrote {len(node_rows)} nodes -> {nodes_csv}")
    print(f"Wrote {len(edge_rows)} edges -> {edges_csv}")


def run_extraction(ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
                    storey_filter=None, type_filter=None, *, project_id=None,
                    source_ifc_file=None, source_file_id=None, discipline="unspecified",
                    coordinate_system_id=None):
    """
    Public entrypoint used by api/routes.py: parse `ifc_path`, apply the
    optional storey/type filters, write nodes_csv/edges_csv, and return
    (node_rows, edge_rows) so the caller can report counts without
    re-reading the CSVs. Safe to call multiple times / concurrently since
    no module-level filter state is mutated.
    """
    model = ifcopenshell.open(ifc_path)
    print(f"Loaded model: {ifc_path}", file=sys.stderr, flush=True)
    node_rows, edge_rows = extract(
        model,
        storey_filter=storey_filter,
        type_filter=type_filter,
        project_id=project_id,
        source_ifc_file=source_ifc_file or Path(ifc_path).name,
        source_file_id=source_file_id,
        discipline=discipline,
        coordinate_system_id=coordinate_system_id,
    )
    write_csvs(node_rows, edge_rows, nodes_csv=nodes_csv, edges_csv=edges_csv)
    return node_rows, edge_rows


def run_multi_extraction(
    file_records,
    nodes_csv=NODES_CSV,
    edges_csv=EDGES_CSV,
    storey_filter=None,
    type_filter=None,
):
    """Extract aligned IFC models into one graph dataset without concatenating IFC files."""
    all_nodes, all_edges = [], []
    per_file = []
    for record in file_records:
        try:
            model = ifcopenshell.open(record["stored_path"])
            nodes, edges = extract(
                model,
                storey_filter=storey_filter,
                type_filter=type_filter,
                project_id=record["project_id"],
                source_ifc_file=record["filename"],
                source_file_id=record["file_id"],
                discipline=record.get("discipline", "unspecified"),
                coordinate_system_id=record["coordinate_system"]["coordinate_fingerprint"],
            )
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            per_file.append({"file_id": record["file_id"], "filename": record["filename"],
                             "status": "processed", "nodes": len(nodes), "edges": len(edges)})
        except Exception as exc:
            per_file.append({"file_id": record["file_id"], "filename": record["filename"],
                             "status": "failed", "nodes": 0, "edges": 0, "error": str(exc)})
    write_csvs(all_nodes, all_edges, nodes_csv=nodes_csv, edges_csv=edges_csv)
    return all_nodes, all_edges, per_file


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", default=IFC_PATH, help="Path to the IFC file")
    parser.add_argument(
        "--storey", action="append", default=None,
        help="Storey name to include (repeatable, e.g. --storey 'BLDG. "
             "1,2,3- LEVEL 5 FLR. FIN.'). Omit to include all storeys.",
    )
    parser.add_argument(
        "--type", action="append", default=None,
        help="IFC type to include (repeatable, e.g. --type IfcWall "
             "--type IfcDoor). Omit to use the full SEMANTIC_TYPES list.",
    )
    args = parser.parse_args()

    run_extraction(args.ifc, storey_filter=args.storey, type_filter=args.type)
