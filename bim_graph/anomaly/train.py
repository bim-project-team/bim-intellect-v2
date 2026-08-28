"""Reproducible CLI and API for training the BIM graph autoencoder."""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

from .dataset import DEFAULT_OUTPUT, load_dataset
from .model import ModelConfig, GraphAutoencoder, node_anomaly_scores, reconstruction_loss, sample_negative_edges

DEFAULT_CHECKPOINT = Path("artifacts/anomaly/graph_autoencoder.pt")
DEFAULT_HISTORY = Path("artifacts/anomaly/training_history.json")


@dataclass
class TrainingConfig:
    seed: int = 42
    epochs: int = 100
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    hidden_dim: int = 64
    latent_dim: int = 16
    dropout: float = 0.1
    alpha: float = 0.7
    beta: float = 0.3
    negative_ratio: float = 1.0
    threshold_percentile: float = 99.0
    device: str = "auto"
    log_every: int = 10


def resolve_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested.startswith("cuda") and not torch.cuda.is_available():
        print("[anomaly] WARNING: CUDA requested but unavailable; using CPU.")
        return torch.device("cpu")
    return torch.device(requested)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def train_model(
    dataset: Mapping[str, Any],
    config: TrainingConfig,
    checkpoint_path: str | Path = DEFAULT_CHECKPOINT,
    history_path: str | Path | None = DEFAULT_HISTORY,
) -> tuple[GraphAutoencoder, list[dict[str, float]], dict[str, Any]]:
    graph = dataset["graph"]
    if graph["x"].shape[0] == 0:
        raise ValueError("Cannot train on an empty graph")
    set_seed(config.seed)
    device = resolve_device(config.device)
    x = graph["x"].to(device)
    edge_index = graph["edge_index"].to(device)
    model_config = ModelConfig(
        input_dim=x.shape[1], hidden_dim=config.hidden_dim,
        latent_dim=config.latent_dim, dropout=config.dropout,
    )
    model = GraphAutoencoder(model_config).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    negative_count = int(edge_index.shape[1] * config.negative_ratio)
    generator = torch.Generator().manual_seed(config.seed)
    negative_edges = sample_negative_edges(x.shape[0], edge_index.cpu(), negative_count, generator).to(device)
    history: list[dict[str, float]] = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        loss, metrics = reconstruction_loss(
            model, x, edge_index, negative_edges, config.alpha, config.beta,
        )
        loss.backward()
        optimizer.step()
        metrics["epoch"] = epoch
        history.append(metrics)
        if epoch == 1 or epoch == config.epochs or epoch % max(config.log_every, 1) == 0:
            print(f"[anomaly] epoch={epoch:04d} loss={metrics['loss']:.6f} "
                  f"feature={metrics['feature_loss']:.6f} structural={metrics['structural_loss']:.6f}")

    combined, feature_error, structural_error = node_anomaly_scores(
        model, x, edge_index, config.alpha, config.beta, config.negative_ratio, config.seed,
    )
    scores = combined.detach().cpu().numpy()
    calibration = {
        "score_mean": float(scores.mean()),
        "score_std": float(scores.std()),
        "threshold_percentile": config.threshold_percentile,
        "threshold": float(np.percentile(scores, config.threshold_percentile)),
        "feature_error_mean": float(feature_error.mean().cpu()),
        "structural_error_mean": float(structural_error.mean().cpu()),
    }
    checkpoint = {
        "schema_version": 1,
        "model_state_dict": model.state_dict(),
        "model_config": model.config_dict(),
        "training_config": asdict(config),
        "feature_metadata": dataset["feature_metadata"],
        "training_history": history,
        "calibration": calibration,
        "dataset_manifest": dataset.get("manifest", []),
    }
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, checkpoint_path)
    if history_path:
        history_path = Path(history_path)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(json.dumps({"config": asdict(config), "history": history, "calibration": calibration}, indent=2), encoding="utf-8")
    return model, history, checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--config", help="Optional JSON object/file overriding training options")
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--hidden-dim", type=int)
    parser.add_argument("--latent-dim", type=int)
    parser.add_argument("--alpha", type=float)
    parser.add_argument("--beta", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--device")
    args = parser.parse_args()
    overrides: dict[str, Any] = {}
    if args.config:
        candidate = Path(args.config)
        overrides.update(json.loads(candidate.read_text(encoding="utf-8") if candidate.exists() else args.config))
    for name in ("epochs", "learning_rate", "hidden_dim", "latent_dim", "alpha", "beta", "seed", "device"):
        value = getattr(args, name)
        if value is not None:
            overrides[name] = value
    config = TrainingConfig(**overrides)
    dataset = load_dataset(args.dataset)
    _, history, checkpoint = train_model(dataset, config, args.checkpoint, args.history)
    print(f"Saved checkpoint -> {args.checkpoint}")
    print(f"Final loss: {history[-1]['loss']:.6f}; anomaly threshold: {checkpoint['calibration']['threshold']:.6f}")


if __name__ == "__main__":
    main()
