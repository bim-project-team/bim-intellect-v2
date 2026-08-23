from __future__ import annotations

import warnings

import pandas as pd
import pytest
import torch

from bim_graph import clash_pipeline
from bim_graph.anomaly.dataset import build_dataset
from bim_graph.anomaly.features import (
    UNKNOWN,
    combine_graphs,
    encode_graph,
    fit_feature_metadata,
    graph_from_records,
    read_graph_csv,
)
from bim_graph.anomaly.inference import GraphAnomalyDetector, enrich_clash_results
from bim_graph.anomaly.model import ModelConfig, GraphAutoencoder, node_anomaly_scores
from bim_graph.anomaly.train import TrainingConfig, train_model


def raw_graph(source="synthetic.ifc"):
    return graph_from_records(
        [
            {"id": "storey", "ifcType": "IfcBuildingStorey", "name": "L1"},
            {"id": "wall", "ifcType": "IfcWall", "name": "Wall", "storeyName": "L1",
             "minX": 0, "minY": 0, "minZ": 0, "maxX": 4, "maxY": 0.2, "maxZ": 3},
            {"id": "door", "ifcType": "IfcDoor", "name": "Door", "storeyName": "L1",
             "minX": 1, "minY": 0, "minZ": 0, "maxX": 2, "maxY": 0.2, "maxZ": 2},
            {"id": "isolated", "ifcType": "IfcProxy", "name": None},
        ],
        [
            {"source_id": "storey", "target_id": "wall", "rel_type": "CONTAINS"},
            {"source_id": "storey", "target_id": "door", "rel_type": "CONTAINS"},
            {"source_id": "wall", "target_id": "door", "rel_type": "CLASHES_WITH"},
        ],
        source,
    )


def encoded_dataset():
    raw = raw_graph()
    metadata = fit_feature_metadata([raw])
    encoded = encode_graph(raw, metadata)
    return {
        "schema_version": 1,
        "graph": combine_graphs([encoded]),
        "graphs": [encoded],
        "feature_metadata": metadata,
        "manifest": [],
    }


def test_ifc_feature_extraction_handles_missing_values_and_drops_clash_edges(tmp_path):
    nodes = tmp_path / "nodes.csv"
    edges = tmp_path / "edges.csv"
    pd.DataFrame([
        {"id": "a", "type": "IfcWall", "name": "W", "min_x": 0, "min_y": 0, "min_z": 0,
         "max_x": 2, "max_y": 0.2, "max_z": 3},
        {"id": "b", "type": "IfcDoor", "name": None},
    ]).to_csv(nodes, index=False)
    pd.DataFrame([
        {"source_id": "a", "target_id": "b", "rel_type": "CONTAINS"},
        {"source_id": "a", "target_id": "missing", "rel_type": "BOUNDS"},
        {"source_id": "a", "target_id": "b", "rel_type": "CLASHES_WITH"},
    ]).to_csv(edges, index=False)
    raw = read_graph_csv(nodes, edges, "sample.ifc")
    metadata = fit_feature_metadata([raw])
    encoded = encode_graph(raw, metadata)
    assert encoded["x"].shape[0] == 2
    assert torch.isfinite(encoded["x"]).all()
    assert encoded["edge_index"].shape == (2, 1)
    assert raw["dropped_edge_count"] == 2


def test_unseen_ifc_type_and_storey_use_unknown_buckets():
    training = raw_graph()
    metadata = fit_feature_metadata([training])
    unseen = graph_from_records([{"id": "x", "ifcType": "IfcUnseenThing", "storeyName": "New Floor"}], [])
    encoded = encode_graph(unseen, metadata)
    names = metadata["feature_names"]
    assert encoded["x"][0, names.index(f"ifc_type:{UNKNOWN}")] == 1
    assert encoded["x"][0, names.index(f"storey:{UNKNOWN}")] == 1


def test_dataset_construction_combines_graph_boundaries(monkeypatch, tmp_path):
    ifc_a, ifc_b = tmp_path / "a.ifc", tmp_path / "b.ifc"
    ifc_a.touch(); ifc_b.touch()

    def fake_extract(path, cache_dir, force):
        graph = raw_graph(path.name)
        return graph, {"path": str(path), "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}

    monkeypatch.setattr("bim_graph.anomaly.dataset.extract_ifc_graph", fake_extract)
    output = tmp_path / "dataset.pt"
    artifact = build_dataset([ifc_a, ifc_b], output, tmp_path / "cache")
    assert output.exists()
    assert artifact["graph"]["graph_ptr"] == [0, 4, 8]
    assert artifact["graph"]["edge_index"].shape[1] == 4


def test_model_forward_pass_and_anomaly_scores_are_per_node():
    graph = encoded_dataset()["graph"]
    model = GraphAutoencoder(ModelConfig(graph["x"].shape[1], hidden_dim=8, latent_dim=4, dropout=0))
    reconstructed, latent = model(graph["x"], graph["edge_index"])
    scores, feature_error, structural_error = node_anomaly_scores(
        model, graph["x"], graph["edge_index"], alpha=0.7, beta=0.3,
    )
    assert reconstructed.shape == graph["x"].shape
    assert latent.shape == (4, 4)
    assert scores.shape == feature_error.shape == structural_error.shape == (4,)
    assert torch.isfinite(scores).all()


def test_checkpoint_save_load_and_score_generation(tmp_path):
    dataset = encoded_dataset()
    checkpoint = tmp_path / "model.pt"
    history_file = tmp_path / "history.json"
    config = TrainingConfig(epochs=2, hidden_dim=8, latent_dim=4, dropout=0, log_every=1)
    _, history, _ = train_model(dataset, config, checkpoint, history_file)
    detector = GraphAnomalyDetector(checkpoint, device="cpu")
    scores = detector.score_graph(dataset["graphs"][0])
    assert checkpoint.exists() and history_file.exists()
    assert len(history) == 2
    assert list(scores["Rank"]) == [1, 2, 3, 4]
    assert scores["Anomaly_Score"].is_monotonic_decreasing


def test_mapping_scores_back_to_clash_elements():
    clashes = [{"a_id": "wall", "b_id": "door", "issue": "CLASH", "metric": 0.4}]
    scores = pd.DataFrame([
        {"IFC_GUID": "wall", "Anomaly_Score": 0.2},
        {"IFC_GUID": "door", "Anomaly_Score": 0.8},
    ])
    enriched = enrich_clash_results(clashes, scores)
    assert enriched[0]["anomaly_score_a"] == 0.2
    assert enriched[0]["anomaly_score_b"] == 0.8
    assert enriched[0]["combined_anomaly_score"] == 0.8
    assert enriched[0]["metric"] == 0.4


class FakeNeo4jClient:
    calls = []

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def verify_connectivity(self): pass
    def run_batched(self, query, rows, batch_size): self.calls.append((query, rows))
    def run(self, query, params=None):
        self.calls.append((query, params))
        if query == clash_pipeline.FETCH_QUERY:
            return []
        if "RETURN r.issue AS issue, count(*) AS n" in query:
            return []
        return []


def test_clash_pipeline_disabled_preserves_summary_and_skips_anomaly(monkeypatch):
    FakeNeo4jClient.calls = []
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", FakeNeo4jClient)
    summary = clash_pipeline.run_clash_detection(anomaly_model_enabled=False)
    assert summary == {"elements_checked": 0, "issues_detected": 0, "issues_by_type": {}}
    assert not any(query == clash_pipeline.ANOMALY_NODE_FETCH_QUERY for query, _ in FakeNeo4jClient.calls)


def test_missing_checkpoint_warns_and_keeps_rule_pipeline_running(monkeypatch, tmp_path):
    FakeNeo4jClient.calls = []
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", FakeNeo4jClient)
    with pytest.warns(RuntimeWarning, match="checkpoint not found"):
        summary = clash_pipeline.run_clash_detection(
            anomaly_model_enabled=True, anomaly_checkpoint=tmp_path / "missing.pt",
        )
    assert summary["anomaly_status"] == "unavailable"
    assert summary["elements_scored"] == 0


def test_enabled_pipeline_scores_once_and_writes_distinct_anomaly_fields(monkeypatch):
    class EnabledClient(FakeNeo4jClient):
        calls = []

        def run(self, query, params=None):
            self.calls.append((query, params))
            if query == clash_pipeline.FETCH_QUERY:
                return [
                    {"id": "a", "type": "IfcBeam", "name": "A", "storey_name": "L1",
                     "min_x": 0, "min_y": 0, "min_z": 0, "max_x": 2, "max_y": 2, "max_z": 2},
                    {"id": "b", "type": "IfcFlowSegment", "name": "B", "storey_name": "L1",
                     "min_x": 1, "min_y": 1, "min_z": 1, "max_x": 3, "max_y": 3, "max_z": 3},
                ]
            if query == clash_pipeline.ANOMALY_NODE_FETCH_QUERY:
                return [{"id": "a", "ifcType": "IfcBeam"}, {"id": "b", "ifcType": "IfcFlowSegment"}]
            if query == clash_pipeline.ANOMALY_EDGE_FETCH_QUERY:
                return [{"source_id": "a", "target_id": "b", "rel_type": "CONTAINS"}]
            if "RETURN r.issue AS issue, count(*) AS n" in query:
                return [{"issue": "CLASH", "n": 1}]
            return []

        def run_batched(self, query, rows, batch_size):
            self.calls.append((query, list(rows)))

    class StubDetector:
        calls = 0

        def score_records(self, nodes, edges, source_file):
            self.calls += 1
            return pd.DataFrame([
                {"IFC_GUID": "a", "Anomaly_Score": 0.25, "Feature_Error": 0.1,
                 "Structural_Error": 0.6, "Is_Anomaly": False},
                {"IFC_GUID": "b", "Anomaly_Score": 0.75, "Feature_Error": 0.7,
                 "Structural_Error": 0.9, "Is_Anomaly": True},
            ])

    detector = StubDetector()
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", EnabledClient)
    summary = clash_pipeline.run_clash_detection(anomaly_model_enabled=True, anomaly_detector=detector)
    assert detector.calls == 1
    assert summary == {"elements_checked": 2, "issues_detected": 1, "issues_by_type": {"CLASH": 1},
                       "anomaly_status": "scored", "elements_scored": 2}
    anomaly_clash_rows = next(rows for query, rows in EnabledClient.calls if query == clash_pipeline.WRITE_CLASH_ANOMALY_QUERY)
    assert anomaly_clash_rows[0]["combined_anomaly_score"] == 0.75
    assert anomaly_clash_rows[0]["metric"] == 1.0
