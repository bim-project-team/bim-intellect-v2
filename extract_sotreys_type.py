"""
Extract all Storeys and IFC Types across every IFC file in dataset/ifc/.

Scans the whole dataset directory (not a single file) so the UI filter
dropdowns can be populated once, up front, before any ingestion run.

Outputs (project root, matching bim_graph/config.py conventions):
  storeys.csv    -> one row per distinct storey name found (deduped across files)
  ifc_types.csv  -> one row per distinct IFC entity type found, with total count

Usage:
    python extract_sotreys_type.py                     # scans dataset/ifc/
    python extract_sotreys_type.py path/to/other/dir    # scans a different dir
    python extract_sotreys_type.py path/to/model.ifc    # scans a single file
"""
import csv
import glob
import os
import sys
from collections import Counter

import ifcopenshell

DEFAULT_IFC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset", "ifc")
STOREYS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storeys.csv")
TYPES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ifc_types.csv")


def _collect_ifc_paths(target):
    if isinstance(target, (list, tuple, set)):
        return sorted(str(path) for path in target if os.path.isfile(path))
    if os.path.isdir(target):
        return sorted(glob.glob(os.path.join(target, "*.ifc")))
    if os.path.isfile(target):
        return [target]
    return []


def extract_storeys(model, source_file, storey_rows, seen_names):
    for storey in model.by_type("IfcBuildingStorey"):
        name = storey.Name or ""
        if name in seen_names:
            continue
        seen_names.add(name)
        storey_rows.append({
            "name": name,
            "long_name": storey.LongName or "",
            "elevation": storey.Elevation if storey.Elevation is not None else "",
            "source_file": os.path.basename(source_file),
        })


def extract_type_counts(model, counter):
    for entity in model:
        counter[entity.is_a()] += 1


def scan_ifc(path):
    """Return per-file filter metadata and prove that the IFC parses successfully."""
    model = ifcopenshell.open(str(path))
    storeys = sorted({(storey.Name or "") for storey in model.by_type("IfcBuildingStorey") if storey.Name})
    counter = Counter(entity.is_a() for entity in model)
    types = [{"type": name, "count": count} for name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]
    return {"schema": model.schema, "storeys": storeys, "types": types}


def main(target=None, storeys_csv=STOREYS_CSV, types_csv=TYPES_CSV):
    target = target or DEFAULT_IFC_DIR
    ifc_paths = _collect_ifc_paths(target)

    if not ifc_paths:
        print(f"No .ifc files found at: {target}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(ifc_paths)} IFC file(s) in {target}", file=sys.stderr)

    storey_rows = []
    seen_names = set()
    type_counter = Counter()

    for path in ifc_paths:
        print(f"Scanning {os.path.basename(path)} ...", file=sys.stderr)
        model = ifcopenshell.open(path)
        schema = model.schema
        if schema.upper() != "IFC2X3":
            print(f"  Warning: {os.path.basename(path)} schema is '{schema}', not IFC2X3.", file=sys.stderr)
        extract_storeys(model, path, storey_rows, seen_names)
        extract_type_counts(model, type_counter)

    storey_rows.sort(key=lambda r: r["name"])
    print(f"Found {len(storey_rows)} distinct storeys across all files", file=sys.stderr)
    with open(storeys_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "long_name", "elevation", "source_file"])
        writer.writeheader()
        writer.writerows(storey_rows)

    type_rows = [
        {"ifc_type": t, "count": c}
        for t, c in sorted(type_counter.items(), key=lambda x: (-x[1], x[0]))
    ]
    print(f"Found {len(type_rows)} distinct IFC types across all files", file=sys.stderr)
    with open(types_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ifc_type", "count"])
        writer.writeheader()
        writer.writerows(type_rows)

    print(f"Wrote {storeys_csv} and {types_csv}", file=sys.stderr)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)
