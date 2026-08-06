"""
IFC extraction script for BIM-Intellect (Dariush's pipeline structure)
Optimized with parallel geometry extraction (Kiarash's enhancement).

Outputs: nodes.csv and edges.csv
"""
import csv
import multiprocessing
import sys
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as element_util
from tqdm import tqdm

# The types we care about for the MVP graph
ELEMENT_TYPES = [
    "IfcStair", "IfcStairFlight", "IfcTransportElement",
    "IfcDoor", "IfcWall", "IfcSpace", "IfcSlab", "IfcColumn"
]

def _make_settings():
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.DISABLE_OPENING_SUBTRACTIONS, True)
    settings.set(settings.APPLY_DEFAULT_MATERIALS, False)
    return settings

def extract_all_geometry(model):
    """Bulk-extract bounding boxes using parallel iterator (Kiarash's optimization)"""
    settings = _make_settings()
    iterator = ifcopenshell.geom.iterator(
        settings, model, multiprocessing.cpu_count()
    )
    bbox_map = {}
    if iterator.initialize():
        pbar = tqdm(desc="Extracting geometry (parallel)", unit="elem", file=sys.stderr)
        while True:
            shape = iterator.get()
            verts = shape.geometry.verts
            if verts:
                xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
                bbox_map[shape.guid] = {
                    "min_x": min(xs), "min_y": min(ys), "min_z": min(zs),
                    "max_x": max(xs), "max_y": max(ys), "max_z": max(zs),
                }
            pbar.update(1)
            if not iterator.next():
                break
        pbar.close()
    return bbox_map

def extract_graph(ifc_path, nodes_csv="nodes.csv", edges_csv="edges.csv"):
    print(f"Loading model from {ifc_path}...", file=sys.stderr, flush=True)
    model = ifcopenshell.open(ifc_path)
    
    print("Model loaded. Extracting geometry...", file=sys.stderr, flush=True)
    bbox_map = extract_all_geometry(model)
    
    nodes_data = []
    edges_data = []
    
    print("Building nodes and relationships...", file=sys.stderr, flush=True)
    for etype in ELEMENT_TYPES:
        elements = model.by_type(etype)
        for elem in tqdm(elements, desc=f"Parsing {etype}", unit="elem", file=sys.stderr):
            # 1. Node Data
            container = element_util.get_container(elem)
            storey_id = container.GlobalId if container else ""
            storey_name = container.Name if container else ""
            
            bbox = bbox_map.get(elem.GlobalId, {})
            
            nodes_data.append({
                "id": elem.GlobalId,
                "type": etype,
                "name": elem.Name or "",
                "storey_id": storey_id,
                "storey_name": storey_name,
                "min_x": bbox.get("min_x", ""),
                "min_y": bbox.get("min_y", ""),
                "min_z": bbox.get("min_z", ""),
                "max_x": bbox.get("max_x", ""),
                "max_y": bbox.get("max_y", ""),
                "max_z": bbox.get("max_z", "")
            })
            
            # 2. Edge Data (Relationships)
            # Find what this element aggregates/contains/bounds
            # (Matches Dariush's relationship scheme)
            if hasattr(elem, "IsDecomposedBy"):
                for rel in elem.IsDecomposedBy:
                    for related in rel.RelatedObjects:
                        edges_data.append({"source_id": elem.GlobalId, "target_id": related.GlobalId, "rel_type": "AGGREGATES"})
                        
            if hasattr(elem, "ContainsElements"):
                for rel in elem.ContainsElements:
                    for related in rel.RelatedElements:
                        edges_data.append({"source_id": elem.GlobalId, "target_id": related.GlobalId, "rel_type": "CONTAINS"})

            if hasattr(elem, "ProvidedBoundaries"):
                for rel in elem.ProvidedBoundaries:
                    if hasattr(rel, "RelatingSpace"):
                        edges_data.append({"source_id": elem.GlobalId, "target_id": rel.RelatingSpace.GlobalId, "rel_type": "BOUNDS"})

    # Write nodes.csv
    print(f"Writing {len(nodes_data)} nodes to {nodes_csv}...", file=sys.stderr)
    with open(nodes_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "type", "name", "storey_id", "storey_name", "min_x", "min_y", "min_z", "max_x", "max_y", "max_z"])
        writer.writeheader()
        writer.writerows(nodes_data)

    # Write edges.csv
    print(f"Writing {len(edges_data)} edges to {edges_csv}...", file=sys.stderr)
    with open(edges_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source_id", "target_id", "rel_type"])
        writer.writeheader()
        writer.writerows(edges_data)

    print("Done! Ready for load_to_neo4j.py", file=sys.stderr)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python extract_graph.py <path_to_ifc>")
        sys.exit(1)
    
    extract_graph(sys.argv[1])