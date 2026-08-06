"""
extract_graph.py
-----------------
Extracts semantic building elements (NOT raw geometry entities) from an IFC
file into two CSVs shaped for a fast Neo4j `LOAD CSV` bulk import:

    nodes.csv  - one row per building element (wall, door, space, etc.)
    edges.csv  - one row per relationship (containment, aggregation,
                 space-boundary, port-connection)

Why CSV + LOAD CSV instead of the Python driver in a loop:
On a file this size (2.7M raw entities), inserting node-by-node via the
Neo4j Python driver is orders of magnitude slower than a single
`LOAD CSV ... CREATE` pass. Generate the CSVs here, then bulk-import.

Install once:
    pip install ifcopenshell

Run:
    python extract_graph.py
"""

import csv
import sys
import multiprocessing
import ifcopenshell
import ifcopenshell.geom as geom
import ifcopenshell.util.element as Element
from tqdm import tqdm

IFC_PATH = "dataset/210_King_Merged.ifc"
NODES_CSV = "nodes.csv"
EDGES_CSV = "edges.csv"

# --- Filters ---------------------------------------------------------------
# Pass storey_filter / type_filter as arguments to run_extraction() /
# extract() rather than module globals - this keeps the module safe to
# import and call concurrently (e.g. from the FastAPI /ingest endpoint)
# without one request's filter leaking into another's.

# Only these types become graph nodes. Extend as needed once you've
# decided which disciplines (arch / structural / MEP) are in scope.
# IfcStairFlight and IfcTransportElement (elevators) added for egress /
# accessibility rule-checking later in the pipeline.
SEMANTIC_TYPES = [
    "IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcWindow", "IfcColumn",
    "IfcBeam", "IfcSlab", "IfcStair", "IfcStairFlight", "IfcTransportElement",
    "IfcRailing", "IfcRoof", "IfcCurtainWall", "IfcCovering", "IfcSpace",
    "IfcBuildingElementProxy", "IfcFlowSegment", "IfcFlowFitting",
    "IfcFlowTerminal", "IfcFlowController",
]

geom_settings = geom.settings()
geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
# Step 1 perf tweaks: these skip work inside each create_shape() call
# without changing the lazy, per-element/filtered call pattern below.
# Bounding boxes don't need door/window cutouts subtracted from
# walls/slabs - the outer envelope is unaffected in normal cases - so
# this removes the expensive boolean CSG step.
geom_settings.set(geom_settings.DISABLE_OPENING_SUBTRACTIONS, True)
# Skip material resolution - not needed for bbox extraction.
geom_settings.set(geom_settings.APPLY_DEFAULT_MATERIALS, False)


def extract_geometry_for(model, elements):
    """
    Step 3: bulk-extract bounding boxes for exactly the given `elements`
    list (NOT the whole model), using IfcOpenShell's multi-threaded
    iterator restricted via `include=`. This gives us the iterator's
    parallelism without paying for geometry across the entire merged
    multi-building file when only a storey/type-filtered subset was asked
    for - the mistake in the earlier whole-model version.

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


def storey_of(element, storey_cache={}):
    """Walk containment relationships to find the owning IfcBuildingStorey."""
    container = Element.get_container(element)
    if container is None:
        return None
    return container.GlobalId, container.Name


def extract(model, storey_filter=None, type_filter=None):
    node_rows = []
    edge_rows = []

    # --- spatial hierarchy nodes + edges (Project/Site/Building/Storey) ---
    for rel in model.by_type("IfcRelAggregates"):
        parent = rel.RelatingObject
        for child in rel.RelatedObjects:
            if parent.is_a("IfcSpatialStructureElement") or parent.is_a("IfcProject"):
                edge_rows.append([parent.GlobalId, child.GlobalId, "AGGREGATES"])

    for entity_type in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"):
        for e in model.by_type(entity_type):
            node_rows.append({
                "id": e.GlobalId, "type": e.is_a(), "name": e.Name or "",
                "storey_id": "", "storey_name": "",
                "min_x": "", "min_y": "", "min_z": "",
                "max_x": "", "max_y": "", "max_z": "",
            })

    # --- semantic building element nodes ---
    types_to_extract = type_filter if type_filter else SEMANTIC_TYPES

    # Pass 1: figure out which elements survive the storey/type filter.
    # This is cheap - no geometry touched yet - so it's fine to do in a
    # plain Python loop even across a huge multi-building file.
    candidates = []  # list of (element, storey) tuples
    for entity_type in types_to_extract:
        elements = model.by_type(entity_type)
        for e in tqdm(elements, desc=f"Filtering {entity_type}", unit="elem", file=sys.stderr):
            storey = storey_of(e)
            if storey_filter is not None:
                storey_name = storey[1] if storey else None
                if storey_name not in storey_filter:
                    continue
            candidates.append((e, storey))

    # Pass 2: compute bounding boxes ONLY for the filtered candidates, in
    # one parallel batch - this is the fix for the earlier regression,
    # where the iterator ran over the entire model regardless of filters.
    bbox_map = extract_geometry_for(model, [e for e, _ in candidates])
    print(f"Geometry extracted for {len(bbox_map)} of {len(candidates)} filtered elements.",
          file=sys.stderr, flush=True)

    included_ids = set()  # track which element ids survive filtering,
                           # so downstream edge loops (space boundaries,
                           # ports) only reference elements we kept
    for e, storey in candidates:
        bbox = bbox_map.get(e.GlobalId)
        included_ids.add(e.GlobalId)
        node_rows.append({
            "id": e.GlobalId, "type": e.is_a(), "name": e.Name or "",
            "storey_id": storey[0] if storey else "",
            "storey_name": storey[1] if storey else "",
            "min_x": bbox[0] if bbox else "", "min_y": bbox[1] if bbox else "",
            "min_z": bbox[2] if bbox else "", "max_x": bbox[3] if bbox else "",
            "max_y": bbox[4] if bbox else "", "max_z": bbox[5] if bbox else "",
        })
        if storey:
            edge_rows.append([storey[0], e.GlobalId, "CONTAINS"])

    # --- space boundary edges (space <-> element), useful for clearance rules ---
    # Only kept when both ends survived the type/storey filters above, so
    # edges.csv never references a node id that isn't in nodes.csv.
    for rel in model.by_type("IfcRelSpaceBoundary"):
        space = rel.RelatingSpace
        element = rel.RelatedBuildingElement
        if space and element:
            if space.GlobalId in included_ids and element.GlobalId in included_ids:
                edge_rows.append([space.GlobalId, element.GlobalId, "BOUNDS"])

    # --- MEP port connections (useful if MEP is in scope for clash checks) ---
    for rel in model.by_type("IfcRelConnectsPortToElement"):
        port = rel.RelatingPort
        element = rel.RelatedElement
        if port and element:
            if element.GlobalId in included_ids:
                edge_rows.append([port.GlobalId, element.GlobalId, "PORT_OF"])

    return node_rows, edge_rows


def write_csvs(node_rows, edge_rows, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV):
    # Safety net: drop any edge whose source or target id isn't actually
    # present as a node. This matters because the spatial-hierarchy
    # (AGGREGATES) loop walks the WHOLE file's Project/Site/Building/Storey/
    # Space tree unfiltered, while the semantic-element loop above respects
    # STOREY_FILTER/TYPE_FILTER - so without this check, edges.csv can
    # reference storeys/spaces that were correctly excluded as nodes,
    # and Neo4j's MATCH would just silently skip loading them (no error).
    node_ids = {row["id"] for row in node_rows}
    before = len(edge_rows)
    edge_rows = [e for e in edge_rows if e[0] in node_ids and e[1] in node_ids]
    dropped = before - len(edge_rows)
    if dropped:
        print(f"Dropped {dropped} edge(s) referencing ids outside the extracted node set "
              f"(expected when STOREY_FILTER/TYPE_FILTER narrows the extraction).")

    with open(nodes_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "type", "name", "storey_id", "storey_name",
            "min_x", "min_y", "min_z", "max_x", "max_y", "max_z",
        ])
        writer.writeheader()
        writer.writerows(node_rows)

    with open(edges_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source_id", "target_id", "rel_type"])
        writer.writerows(edge_rows)

    print(f"Wrote {len(node_rows)} nodes -> {nodes_csv}")
    print(f"Wrote {len(edge_rows)} edges -> {edges_csv}")


def run_extraction(ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
                    storey_filter=None, type_filter=None):
    """
    Public entrypoint: parse `ifc_path`, apply the optional storey/type
    filters, write nodes_csv/edges_csv, and return (node_rows, edge_rows)
    so a caller (e.g. the FastAPI /ingest endpoint) can report counts
    without re-reading the CSVs. Safe to call multiple times / concurrently
    since no module-level filter state is mutated.
    """
    model = ifcopenshell.open(ifc_path)
    print(f"Loaded model: {ifc_path}", file=sys.stderr, flush=True)
    node_rows, edge_rows = extract(model, storey_filter=storey_filter, type_filter=type_filter)
    write_csvs(node_rows, edge_rows, nodes_csv=nodes_csv, edges_csv=edges_csv)
    return node_rows, edge_rows


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
