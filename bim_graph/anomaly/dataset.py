"""Reproducible IFC-to-graph dataset builder.

Artifact schema (``torch.save`` / ``.pt``):

``graph``
    ``x`` float tensor [nodes, features], ``edge_index`` long tensor [2, edges],
    ``graph_ptr`` building offsets, GUID/type/name/storey/source arrays.
``graphs``
    Per-building encoded graphs with the same fields (without graph_ptr).
``feature_metadata``
    Categorical vocabularies, ordered feature names, mean, and standard deviation.
``manifest``
    Source IFC path/size/mtime and extraction cache paths.

The extraction cache contains the exact nodes/edges CSV representation already
used by Neo4j, so repeat training does not reparse IFC geometry.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any, Sequence

import torch

from extract_graph import run_extraction

from .features import combine_graphs, encode_graph, fit_feature_metadata, read_graph_csv

DEFAULT_OUTPUT = Path("artifacts/anomaly/bim_graph_dataset.pt")
DEFAULT_CACHE_DIR = Path("artifacts/anomaly/extracted")


def _source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _cache_paths(ifc_path: Path, cache_dir: Path) -> tuple[Path, Path, Path]:
    stem = ifc_path.stem.replace(" ", "_")
    return (
        cache_dir / f"{stem}.nodes.csv",
        cache_dir / f"{stem}.edges.csv",
        cache_dir / f"{stem}.manifest.json",
    )


def _cache_is_current(ifc_path: Path, nodes_csv: Path, edges_csv: Path, manifest_path: Path) -> bool:
    if not nodes_csv.exists() or not edges_csv.exists() or not manifest_path.exists():
        return False
    try:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return cached.get("source") == _source_fingerprint(ifc_path)


def extract_ifc_graph(
    ifc_path: str | Path,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract or reuse one IFC graph and return (raw_graph, manifest_entry)."""
    ifc_path = Path(ifc_path)
    if not ifc_path.exists():
        raise FileNotFoundError(f"IFC file not found: {ifc_path}")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    nodes_csv, edges_csv, manifest_path = _cache_paths(ifc_path, cache_dir)
    cache_hit = not force and _cache_is_current(ifc_path, nodes_csv, edges_csv, manifest_path)
    if not cache_hit:
        run_extraction(str(ifc_path), nodes_csv=str(nodes_csv), edges_csv=str(edges_csv))
        manifest_path.write_text(
            json.dumps({"source": _source_fingerprint(ifc_path)}, indent=2),
            encoding="utf-8",
        )
    graph = read_graph_csv(nodes_csv, edges_csv, source_file=ifc_path.name)
    entry = {
        **_source_fingerprint(ifc_path),
        "nodes_csv": str(nodes_csv.resolve()),
        "edges_csv": str(edges_csv.resolve()),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "cache_hit": cache_hit,
    }
    return graph, entry


def build_dataset(
    ifc_paths: Sequence[str | Path],
    output_path: str | Path = DEFAULT_OUTPUT,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    force_extract: bool = False,
) -> dict[str, Any]:
    """Build, save, and return a combined disconnected IFC graph dataset."""
    paths = [Path(path) for path in ifc_paths]
    if not paths:
        raise ValueError("No IFC files were supplied")
    raw_graphs, manifest = [], []
    for path in paths:
        try:
            graph, entry = extract_ifc_graph(path, cache_dir=cache_dir, force=force_extract)
        except Exception as exc:
            warnings.warn(f"Skipping IFC file {path}: {exc}", RuntimeWarning, stacklevel=2)
            continue
        if not graph["nodes"]:
            warnings.warn(f"Skipping empty IFC graph: {path}", RuntimeWarning, stacklevel=2)
            continue
        raw_graphs.append(graph)
        manifest.append(entry)
    if not raw_graphs:
        raise ValueError("None of the IFC files produced a non-empty graph")

    feature_metadata = fit_feature_metadata(raw_graphs)
    encoded_graphs = [encode_graph(graph, feature_metadata) for graph in raw_graphs]
    artifact = {
        "schema_version": 1,
        "graph": combine_graphs(encoded_graphs),
        "graphs": encoded_graphs,
        "feature_metadata": feature_metadata,
        "manifest": manifest,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(artifact, output_path)
    return artifact


def load_dataset(path: str | Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Processed graph dataset not found: {path}")
    artifact = torch.load(path, map_location="cpu", weights_only=False)
    if artifact.get("schema_version") != 1:
        raise ValueError(f"Unsupported dataset schema version: {artifact.get('schema_version')}")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc-dir", default="dataset/ifc", help="Directory containing IFC files")
    parser.add_argument("--ifc", action="append", help="Explicit IFC path (repeatable; overrides --ifc-dir)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--force-extract", action="store_true")
    args = parser.parse_args()
    paths = [Path(path) for path in args.ifc] if args.ifc else sorted(Path(args.ifc_dir).glob("*.ifc"))
    artifact = build_dataset(paths, args.output, args.cache_dir, args.force_extract)
    graph = artifact["graph"]
    print(f"Saved {len(artifact['graphs'])} graph(s), {graph['x'].shape[0]} nodes, "
          f"{graph['edge_index'].shape[1]} edges, {graph['x'].shape[1]} features -> {args.output}")


if __name__ == "__main__":
    main()
