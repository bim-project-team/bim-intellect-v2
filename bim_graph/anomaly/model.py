"""Small sparse-GCN autoencoder used for unsupervised BIM anomalies."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.nn import functional as F


@dataclass
class ModelConfig:
    input_dim: int
    hidden_dim: int = 64
    latent_dim: int = 16
    dropout: float = 0.1


def normalized_adjacency(edge_index: torch.Tensor, num_nodes: int, device: torch.device) -> torch.Tensor:
    """Build sparse symmetric D^-1/2 (A + I) D^-1/2."""
    if num_nodes == 0:
        indices = torch.empty((2, 0), dtype=torch.long, device=device)
        return torch.sparse_coo_tensor(indices, torch.empty(0, device=device), (0, 0)).coalesce()
    edge_index = edge_index.to(device)
    loops = torch.arange(num_nodes, device=device, dtype=torch.long).repeat(2, 1)
    if edge_index.numel():
        reverse = edge_index.flip(0)
        indices = torch.cat([edge_index, reverse, loops], dim=1)
    else:
        indices = loops
    values = torch.ones(indices.shape[1], device=device)
    adjacency = torch.sparse_coo_tensor(indices, values, (num_nodes, num_nodes)).coalesce()
    indices = adjacency.indices()
    values = adjacency.values()
    degree = torch.zeros(num_nodes, device=device).index_add_(0, indices[0], values)
    inv_sqrt = degree.clamp_min(1).pow(-0.5)
    normalized_values = values * inv_sqrt[indices[0]] * inv_sqrt[indices[1]]
    return torch.sparse_coo_tensor(indices, normalized_values, (num_nodes, num_nodes)).coalesce()


class GraphConvolution(nn.Module):
    def __init__(self, in_features: int, out_features: int):
        super().__init__()
        self.linear = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor, adjacency: torch.Tensor) -> torch.Tensor:
        return torch.sparse.mm(adjacency, self.linear(x))


class GraphAutoencoder(nn.Module):
    """Two-layer GCN encoder with feature and dot-product link decoders."""

    def __init__(self, config: ModelConfig):
        super().__init__()
        self.config = config
        self.gcn1 = GraphConvolution(config.input_dim, config.hidden_dim)
        self.gcn2 = GraphConvolution(config.hidden_dim, config.latent_dim)
        self.feature_decoder = nn.Sequential(
            nn.Linear(config.latent_dim, config.hidden_dim),
            nn.ReLU(),
            nn.Linear(config.hidden_dim, config.input_dim),
        )

    def encode(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        adjacency = normalized_adjacency(edge_index, x.shape[0], x.device)
        hidden = F.relu(self.gcn1(x, adjacency))
        hidden = F.dropout(hidden, p=self.config.dropout, training=self.training)
        return self.gcn2(hidden, adjacency)

    def decode_links(self, z: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        if edge_index.numel() == 0:
            return torch.empty(0, device=z.device)
        return (z[edge_index[0]] * z[edge_index[1]]).sum(dim=1) / math.sqrt(z.shape[1])

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        z = self.encode(x, edge_index)
        return self.feature_decoder(z), z

    def config_dict(self) -> dict:
        return asdict(self.config)


def sample_negative_edges(
    num_nodes: int,
    positive_edge_index: torch.Tensor,
    num_samples: int,
    generator: torch.Generator | None = None,
) -> torch.Tensor:
    """Sample directed non-edges without allocating an N x N matrix."""
    if num_nodes < 2 or num_samples <= 0:
        return torch.empty((2, 0), dtype=torch.long)
    positives = set()
    for source, target in positive_edge_index.detach().cpu().t().tolist():
        positives.add((source, target))
        positives.add((target, source))
    maximum = num_nodes * (num_nodes - 1) - len(positives)
    target_count = min(num_samples, max(maximum, 0))
    negatives: set[tuple[int, int]] = set()
    attempts = 0
    max_attempts = max(100, target_count * 20)
    while len(negatives) < target_count and attempts < max_attempts:
        batch = max(32, (target_count - len(negatives)) * 2)
        sources = torch.randint(num_nodes, (batch,), generator=generator).tolist()
        targets = torch.randint(num_nodes, (batch,), generator=generator).tolist()
        for source, target in zip(sources, targets):
            if source != target and (source, target) not in positives:
                negatives.add((source, target))
                if len(negatives) == target_count:
                    break
        attempts += batch
    if not negatives:
        return torch.empty((2, 0), dtype=torch.long)
    return torch.tensor(sorted(negatives), dtype=torch.long).t().contiguous()


def reconstruction_loss(
    model: GraphAutoencoder,
    x: torch.Tensor,
    edge_index: torch.Tensor,
    negative_edge_index: torch.Tensor,
    alpha: float,
    beta: float,
) -> tuple[torch.Tensor, dict[str, float]]:
    reconstructed, z = model(x, edge_index)
    feature_loss = F.mse_loss(reconstructed, x)
    positive_logits = model.decode_links(z, edge_index)
    negative_logits = model.decode_links(z, negative_edge_index.to(x.device))
    terms = []
    if positive_logits.numel():
        terms.append(F.binary_cross_entropy_with_logits(positive_logits, torch.ones_like(positive_logits)))
    if negative_logits.numel():
        terms.append(F.binary_cross_entropy_with_logits(negative_logits, torch.zeros_like(negative_logits)))
    structural_loss = torch.stack(terms).mean() if terms else torch.zeros((), device=x.device)
    loss = alpha * feature_loss + beta * structural_loss
    return loss, {"loss": float(loss.detach()), "feature_loss": float(feature_loss.detach()), "structural_loss": float(structural_loss.detach())}


@torch.no_grad()
def node_anomaly_scores(
    model: GraphAutoencoder,
    x: torch.Tensor,
    edge_index: torch.Tensor,
    alpha: float,
    beta: float,
    negative_ratio: float = 1.0,
    seed: int = 42,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Return combined, feature, and incident-link reconstruction errors."""
    model.eval()
    reconstructed, z = model(x, edge_index)
    feature_error = (reconstructed - x).pow(2).mean(dim=1)
    num_negative = max(int(edge_index.shape[1] * negative_ratio), x.shape[0]) if x.shape[0] else 0
    generator = torch.Generator().manual_seed(seed)
    negative_edges = sample_negative_edges(x.shape[0], edge_index.cpu(), num_negative, generator).to(x.device)
    structural_sum = torch.zeros(x.shape[0], device=x.device)
    structural_count = torch.zeros(x.shape[0], device=x.device)
    for edges, target in ((edge_index.to(x.device), 1.0), (negative_edges, 0.0)):
        if not edges.numel():
            continue
        logits = model.decode_links(z, edges)
        errors = F.binary_cross_entropy_with_logits(logits, torch.full_like(logits, target), reduction="none")
        for endpoint in (edges[0], edges[1]):
            structural_sum.index_add_(0, endpoint, errors)
            structural_count.index_add_(0, endpoint, torch.ones_like(errors))
    structural_error = structural_sum / structural_count.clamp_min(1.0)
    combined = alpha * feature_error + beta * structural_error
    return combined, feature_error, structural_error
