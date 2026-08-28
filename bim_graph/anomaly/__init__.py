"""Lightweight, optional graph anomaly detection for BIM element graphs.

Modules are intentionally not imported eagerly so every CLI can be executed
with ``python -m`` without side effects or duplicate-module warnings.
"""

__all__ = ["dataset", "features", "inference", "model", "train"]
