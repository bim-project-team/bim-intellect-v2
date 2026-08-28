"""Feature engineering for the existing IFC/Neo4j graph representation.

The encoder deliberately uses only data produced by ``extract_graph.py``:
IFC type, storey, axis-aligned bounding boxes, and typed graph topology.
Clash results are never accepted as input edges or features.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import torch

UNKNOWN = "__UNKNOWN__"
SCHEMA_VERSION = 1

BASE_NUMERIC_FEATURES = [
    "has_bbox",
    "center_x", "center_y", "center_z",
    "extent_x", "extent_y", "extent_z",
    "log1p_bbox_volume",
    "degree", "in_degree", "out_degree",
    "neighbor_degree_mean", "is_isolated", "has_storey",
]


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _clean_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def read_graph_csv(
    nodes_csv: str | Path,
    edges_csv: str | Path,
    source_file: str | Path | None = None,
) -> dict[str, Any]:
    """Read extractor CSVs, deduplicate nodes, and discard dangling edges."""
    nodes_df = pd.read_csv(nodes_csv, encoding="utf-8")
    edges_df = pd.read_csv(edges_csv, encoding="utf-8")
    required_nodes = {"id", "type"}
    required_edges = {"source_id", "target_id", "rel_type"}
    if not required_nodes.issubset(nodes_df.columns):
        raise ValueError(f"Node CSV is missing columns: {sorted(required_nodes - set(nodes_df.columns))}")
    if not required_edges.issubset(edges_df.columns):
        raise ValueError(f"Edge CSV is missing columns: {sorted(required_edges - set(edges_df.columns))}")

    nodes_df = nodes_df.drop_duplicates(subset=["id"], keep="first")
    nodes = []
    for row in nodes_df.to_dict("records"):
        nodes.append({
            "id": _clean_text(row.get("id")),
            "type": _clean_text(row.get("type")) or UNKNOWN,
            "name": _clean_text(row.get("name")),
            "storey_id": _clean_text(row.get("storey_id")),
            "storey_name": _clean_text(row.get("storey_name")),
            **{key: _clean_number(row.get(key)) for key in (
                "min_x", "min_y", "min_z", "max_x", "max_y", "max_z"
            )},
        })
    missing_ids = sum(not node["id"] for node in nodes)
    if missing_ids:
        warnings.warn(
            f"{missing_ids} node(s) have no IFC GUID; they will be scored but cannot be joined to relationships.",
            RuntimeWarning,
            stacklevel=2,
        )
    node_ids = {node["id"] for node in nodes if node["id"]}
    edges = []
    for row in edges_df.to_dict("records"):
        source = _clean_text(row.get("source_id"))
        target = _clean_text(row.get("target_id"))
        rel_type = _clean_text(row.get("rel_type")) or UNKNOWN
        if source in node_ids and target in node_ids and rel_type != "CLASHES_WITH":
            edges.append({"source_id": source, "target_id": target, "rel_type": rel_type})
    return {
        "nodes": nodes,
        "edges": edges,
        "source_file": str(source_file or nodes_csv),
        "dropped_edge_count": len(edges_df) - len(edges),
    }


def graph_from_records(
    node_rows: Sequence[Mapping[str, Any]],
    edge_rows: Sequence[Mapping[str, Any]],
    source_file: str = "<records>",
) -> dict[str, Any]:
    """Adapt Neo4j-style rows (camelCase) or extractor-style rows."""
    nodes = []
    for row in node_rows:
        nodes.append({
            "id": _clean_text(row.get("id")),
            "type": _clean_text(row.get("type", row.get("ifcType"))) or UNKNOWN,
            "name": _clean_text(row.get("name")),
            "storey_id": _clean_text(row.get("storey_id", row.get("storeyId"))),
            "storey_name": _clean_text(row.get("storey_name", row.get("storeyName"))),
            "min_x": _clean_number(row.get("min_x", row.get("minX"))),
            "min_y": _clean_number(row.get("min_y", row.get("minY"))),
            "min_z": _clean_number(row.get("min_z", row.get("minZ"))),
            "max_x": _clean_number(row.get("max_x", row.get("maxX"))),
            "max_y": _clean_number(row.get("max_y", row.get("maxY"))),
            "max_z": _clean_number(row.get("max_z", row.get("maxZ"))),
        })
    missing_ids = sum(not node["id"] for node in nodes)
    if missing_ids:
        warnings.warn(
            f"{missing_ids} graph record(s) have no IFC GUID; they will be scored without relationship mapping.",
            RuntimeWarning,
            stacklevel=2,
        )
    ids = {node["id"] for node in nodes if node["id"]}
    edges = []
    for row in edge_rows:
        source = _clean_text(row.get("source_id", row.get("source")))
        target = _clean_text(row.get("target_id", row.get("target")))
        rel_type = _clean_text(row.get("rel_type", row.get("type"))) or UNKNOWN
        if source in ids and target in ids and rel_type != "CLASHES_WITH":
            edges.append({"source_id": source, "target_id": target, "rel_type": rel_type})
    return {"nodes": nodes, "edges": edges, "source_file": source_file, "dropped_edge_count": 0}


def _topology(graph: Mapping[str, Any], relationship_types: Sequence[str]) -> tuple[np.ndarray, list[str]]:
    nodes = graph["nodes"]
    id_to_idx = {node["id"]: index for index, node in enumerate(nodes)}
    n = len(nodes)
    in_degree = np.zeros(n, dtype=np.float32)
    out_degree = np.zeros(n, dtype=np.float32)
    rel_in = {rel: np.zeros(n, dtype=np.float32) for rel in relationship_types}
    rel_out = {rel: np.zeros(n, dtype=np.float32) for rel in relationship_types}
    neighbors: list[set[int]] = [set() for _ in range(n)]
    valid_rel_types = set(relationship_types)
    for edge in graph["edges"]:
        source = id_to_idx.get(edge["source_id"])
        target = id_to_idx.get(edge["target_id"])
        if source is None or target is None:
            continue
        rel = edge["rel_type"] if edge["rel_type"] in valid_rel_types else UNKNOWN
        out_degree[source] += 1
        in_degree[target] += 1
        rel_out[rel][source] += 1
        rel_in[rel][target] += 1
        neighbors[source].add(target)
        neighbors[target].add(source)
    degree = np.asarray([len(values) for values in neighbors], dtype=np.float32)
    neighbor_mean = np.zeros(n, dtype=np.float32)
    for index, values in enumerate(neighbors):
        if values:
            neighbor_mean[index] = float(np.mean(degree[list(values)]))
    columns = [degree, in_degree, out_degree, neighbor_mean, (degree == 0).astype(np.float32)]
    names = ["degree", "in_degree", "out_degree", "neighbor_degree_mean", "is_isolated"]
    for rel in relationship_types:
        columns.extend([rel_in[rel], rel_out[rel]])
        names.extend([f"in_{rel}", f"out_{rel}"])
    return np.column_stack(columns) if n else np.empty((0, len(names)), dtype=np.float32), names


def _raw_numeric(graph: Mapping[str, Any], relationship_types: Sequence[str]) -> tuple[np.ndarray, list[str]]:
    rows = []
    for node in graph["nodes"]:
        coords = [node.get(key) for key in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")]
        has_bbox = float(all(value is not None for value in coords))
        if has_bbox:
            min_x, min_y, min_z, max_x, max_y, max_z = (float(value) for value in coords)
            centers = [(min_x + max_x) / 2, (min_y + max_y) / 2, (min_z + max_z) / 2]
            extents = [max(max_x - min_x, 0.0), max(max_y - min_y, 0.0), max(max_z - min_z, 0.0)]
            log_volume = math.log1p(extents[0] * extents[1] * extents[2])
        else:
            centers, extents, log_volume = [0.0] * 3, [0.0] * 3, 0.0
        rows.append([has_bbox, *centers, *extents, log_volume])
    geometry = np.asarray(rows, dtype=np.float32) if rows else np.empty((0, 8), dtype=np.float32)
    topology, topology_names = _topology(graph, relationship_types)
    has_storey = np.asarray([[float(bool(node.get("storey_name")))] for node in graph["nodes"]], dtype=np.float32)
    numeric = np.column_stack([geometry, topology[:, :5], has_storey, topology[:, 5:]]) if rows else np.empty((0, 14 + max(0, len(topology_names) - 5)), dtype=np.float32)
    names = BASE_NUMERIC_FEATURES + topology_names[5:]
    return numeric, names


def fit_feature_metadata(graphs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not graphs or not any(graph["nodes"] for graph in graphs):
        raise ValueError("Cannot fit feature metadata on an empty dataset")
    entity_types = sorted({node["type"] for graph in graphs for node in graph["nodes"] if node["type"] != UNKNOWN}) + [UNKNOWN]
    storeys = sorted({node["storey_name"] for graph in graphs for node in graph["nodes"] if node["storey_name"]}) + [UNKNOWN]
    relationship_types = sorted({edge["rel_type"] for graph in graphs for edge in graph["edges"] if edge["rel_type"] != "CLASHES_WITH" and edge["rel_type"] != UNKNOWN}) + [UNKNOWN]
    arrays = []
    numeric_names = None
    for graph in graphs:
        values, names = _raw_numeric(graph, relationship_types)
        arrays.append(values)
        numeric_names = names
    all_numeric = np.concatenate(arrays, axis=0)
    mean = all_numeric.mean(axis=0)
    std = all_numeric.std(axis=0)
    std[std < 1e-6] = 1.0
    feature_names = (
        [f"numeric:{name}" for name in numeric_names]
        + [f"ifc_type:{value}" for value in entity_types]
        + [f"storey:{value}" for value in storeys]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "numeric_feature_names": numeric_names,
        "numeric_mean": mean.tolist(),
        "numeric_std": std.tolist(),
        "entity_types": entity_types,
        "storeys": storeys,
        "relationship_types": relationship_types,
        "feature_names": feature_names,
        "input_dim": len(feature_names),
    }


def encode_graph(graph: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Encode one raw graph with already-fitted, checkpointable metadata."""
    nodes = graph["nodes"]
    numeric, names = _raw_numeric(graph, metadata["relationship_types"])
    if list(names) != list(metadata["numeric_feature_names"]):
        raise ValueError("Feature metadata is incompatible with this encoder version")
    mean = np.asarray(metadata["numeric_mean"], dtype=np.float32)
    std = np.asarray(metadata["numeric_std"], dtype=np.float32)
    normalized = (numeric - mean) / std if len(numeric) else numeric

    type_to_idx = {value: index for index, value in enumerate(metadata["entity_types"])}
    storey_to_idx = {value: index for index, value in enumerate(metadata["storeys"])}
    type_features = np.zeros((len(nodes), len(type_to_idx)), dtype=np.float32)
    storey_features = np.zeros((len(nodes), len(storey_to_idx)), dtype=np.float32)
    for index, node in enumerate(nodes):
        type_features[index, type_to_idx.get(node["type"], type_to_idx[UNKNOWN])] = 1.0
        storey = node["storey_name"] or UNKNOWN
        storey_features[index, storey_to_idx.get(storey, storey_to_idx[UNKNOWN])] = 1.0
    x = np.column_stack([normalized, type_features, storey_features]).astype(np.float32)

    id_to_idx = {node["id"]: index for index, node in enumerate(nodes)}
    edge_pairs = [
        (id_to_idx[edge["source_id"]], id_to_idx[edge["target_id"]])
        for edge in graph["edges"]
        if edge["source_id"] in id_to_idx and edge["target_id"] in id_to_idx
    ]
    edge_index = torch.tensor(edge_pairs, dtype=torch.long).t().contiguous() if edge_pairs else torch.empty((2, 0), dtype=torch.long)
    return {
        "x": torch.from_numpy(x),
        "edge_index": edge_index,
        "node_ids": [node["id"] for node in nodes],
        "ifc_guids": [node["id"] for node in nodes],
        "entity_types": [node["type"] for node in nodes],
        "element_names": [node["name"] for node in nodes],
        "storeys": [node["storey_name"] for node in nodes],
        "source_files": [str(graph["source_file"])] * len(nodes),
        "relationship_types": [edge["rel_type"] for edge in graph["edges"]],
        "dropped_edge_count": int(graph.get("dropped_edge_count", 0)),
    }


def combine_graphs(graphs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine disconnected encoded buildings while retaining graph offsets."""
    if not graphs:
        raise ValueError("No graphs were provided")
    offsets = [0]
    edge_indexes = []
    combined: dict[str, Any] = {
        key: [] for key in ("node_ids", "ifc_guids", "entity_types", "element_names", "storeys", "source_files", "relationship_types")
    }
    xs = []
    for graph in graphs:
        xs.append(graph["x"])
        edge_indexes.append(graph["edge_index"] + offsets[-1])
        offsets.append(offsets[-1] + graph["x"].shape[0])
        for key in combined:
            combined[key].extend(graph[key])
    combined.update({
        "x": torch.cat(xs, dim=0),
        "edge_index": torch.cat(edge_indexes, dim=1) if edge_indexes else torch.empty((2, 0), dtype=torch.long),
        "graph_ptr": offsets,
    })
    return combined
