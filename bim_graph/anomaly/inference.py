"""Checkpoint loading, BIM graph scoring, CSV export, and clash enrichment."""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import torch

from .dataset import DEFAULT_CACHE_DIR, extract_ifc_graph
from .features import encode_graph, graph_from_records, read_graph_csv
from .model import ModelConfig, GraphAutoencoder, node_anomaly_scores
from .train import DEFAULT_CHECKPOINT, resolve_device

DEFAULT_SCORES = Path("artifacts/anomaly/anomaly_scores.csv")


class GraphAnomalyDetector:
    """Load one checkpoint once and score any compatible IFC graph."""

    def __init__(self, checkpoint_path: str | Path = DEFAULT_CHECKPOINT, device: str = "auto"):
        checkpoint_path = Path(checkpoint_path)
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Anomaly checkpoint not found: {checkpoint_path}. Run `python -m bim_graph.anomaly.train` first."
            )
        self.device = resolve_device(device)
        self.checkpoint = torch.load(checkpoint_path, map_location=self.device, weights_only=False)
        if self.checkpoint.get("schema_version") != 1:
            raise ValueError(f"Unsupported checkpoint schema: {self.checkpoint.get('schema_version')}")
        self.feature_metadata = self.checkpoint["feature_metadata"]
        self.training_config = self.checkpoint["training_config"]
        self.calibration = self.checkpoint.get("calibration", {})
        self.model = GraphAutoencoder(ModelConfig(**self.checkpoint["model_config"])).to(self.device)
        self.model.load_state_dict(self.checkpoint["model_state_dict"])
        self.model.eval()

    def score_graph(self, encoded_graph: Mapping[str, Any]) -> pd.DataFrame:
        x = encoded_graph["x"].to(self.device)
        if x.shape[0] == 0:
            warnings.warn("Cannot score an empty graph; returning an empty table.", RuntimeWarning, stacklevel=2)
            return pd.DataFrame(columns=[
                "IFC_GUID", "Entity_Type", "Anomaly_Score", "Rank", "Source_File",
                "Feature_Error", "Structural_Error", "Storey", "Element_Name", "Is_Anomaly",
            ])
        edge_index = encoded_graph["edge_index"].to(self.device)
        scores, feature_error, structural_error = node_anomaly_scores(
            self.model, x, edge_index,
            alpha=float(self.training_config["alpha"]),
            beta=float(self.training_config["beta"]),
            negative_ratio=float(self.training_config.get("negative_ratio", 1.0)),
            seed=int(self.training_config.get("seed", 42)),
        )
        threshold = float(self.calibration.get("threshold", float("inf")))
        frame = pd.DataFrame({
            "IFC_GUID": encoded_graph["ifc_guids"],
            "Entity_Type": encoded_graph["entity_types"],
            "Anomaly_Score": scores.cpu().numpy(),
            "Source_File": encoded_graph["source_files"],
            "Feature_Error": feature_error.cpu().numpy(),
            "Structural_Error": structural_error.cpu().numpy(),
            "Storey": encoded_graph["storeys"],
            "Element_Name": encoded_graph["element_names"],
        }).sort_values("Anomaly_Score", ascending=False, kind="stable").reset_index(drop=True)
        frame.insert(3, "Rank", range(1, len(frame) + 1))
        frame["Is_Anomaly"] = frame["Anomaly_Score"] >= threshold
        return frame

    def score_raw_graph(self, raw_graph: Mapping[str, Any]) -> pd.DataFrame:
        return self.score_graph(encode_graph(raw_graph, self.feature_metadata))

    def score_csv(self, nodes_csv: str | Path, edges_csv: str | Path, source_file: str | None = None) -> pd.DataFrame:
        return self.score_raw_graph(read_graph_csv(nodes_csv, edges_csv, source_file))

    def score_ifc(
        self,
        ifc_path: str | Path,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        force_extract: bool = False,
    ) -> pd.DataFrame:
        raw_graph, _ = extract_ifc_graph(ifc_path, cache_dir, force_extract)
        return self.score_raw_graph(raw_graph)

    def score_records(
        self,
        node_rows: Sequence[Mapping[str, Any]],
        edge_rows: Sequence[Mapping[str, Any]],
        source_file: str = "<neo4j>",
    ) -> pd.DataFrame:
        return self.score_raw_graph(graph_from_records(node_rows, edge_rows, source_file))


def enrich_clash_results(
    clashes: Sequence[Mapping[str, Any]],
    anomaly_scores: pd.DataFrame | Sequence[Mapping[str, Any]],
    strategy: str = "max",
) -> list[dict[str, Any]]:
    """Add distinct ML fields without altering rule issue/metric semantics."""
    rows = anomaly_scores.to_dict("records") if isinstance(anomaly_scores, pd.DataFrame) else list(anomaly_scores)
    mapping = {
        str(row.get("IFC_GUID", row.get("id"))): float(row.get("Anomaly_Score", row.get("anomaly_score")))
        for row in rows
        if row.get("IFC_GUID", row.get("id")) is not None and row.get("Anomaly_Score", row.get("anomaly_score")) is not None
    }
    if strategy not in {"max", "mean"}:
        raise ValueError("Anomaly combination strategy must be 'max' or 'mean'")
    enriched = []
    for clash in clashes:
        row = dict(clash)
        score_a = mapping.get(str(row.get("a_id")))
        score_b = mapping.get(str(row.get("b_id")))
        available = [score for score in (score_a, score_b) if score is not None]
        combined = (max(available) if strategy == "max" else sum(available) / len(available)) if available else None
        row.update({
            "anomaly_score_a": score_a,
            "anomaly_score_b": score_b,
            "combined_anomaly_score": combined,
        })
        enriched.append(row)
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ifc", help="IFC file to extract/cache and score")
    source.add_argument("--nodes-csv", help="Existing nodes CSV (requires --edges-csv)")
    parser.add_argument("--edges-csv")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--force-extract", action="store_true")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", default=str(DEFAULT_SCORES))
    args = parser.parse_args()
    detector = GraphAnomalyDetector(args.checkpoint, args.device)
    if args.ifc:
        scores = detector.score_ifc(args.ifc, args.cache_dir, args.force_extract)
    else:
        if not args.edges_csv:
            parser.error("--edges-csv is required with --nodes-csv")
        scores = detector.score_csv(args.nodes_csv, args.edges_csv, source_file=Path(args.nodes_csv).stem)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    scores.to_csv(output, index=False)
    print(scores.head(20).to_string(index=False))
    print(f"Saved {len(scores)} ranked element scores -> {output}")


if __name__ == "__main__":
    main()
