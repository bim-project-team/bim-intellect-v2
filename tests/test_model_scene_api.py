"""HTTP contract tests for the 3D viewer endpoints.

Neo4j is not required: the graph-backed endpoint is exercised for its failure
contract and its parameter construction, which is where the correctness risk is.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import routes
from bim_graph import scene_export
from bim_graph.scene_export import UNASSIGNED_SCENE_KEY, merge_manifest, scene_key, scene_path
from main import app

client = TestClient(app)


@pytest.fixture()
def scenes_root(tmp_path, monkeypatch):
    """Redirect scene storage for both the writer and the API's path resolution."""
    root = tmp_path / "scenes"
    monkeypatch.setattr(scene_export, "SCENES_ROOT", root)
    monkeypatch.setattr(routes, "SCENES_ROOT", root)
    return root


def _publish_scene(project="demo", file_id="f1", storey="Level 5", payload=b"glTF-stub"):
    key = scene_key(storey)
    path = scene_path(project, file_id, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    merge_manifest(project, {
        "file_id": file_id, "source_ifc_file": "model.ifc", "unit_scale_to_metre": 0.3048,
        "generated_at": "2026-01-01T00:00:00+00:00", "failed_elements": 0,
        "scenes": [{"scene_key": key, "storey_name": storey,
                    "element_count": 3, "bytes": len(payload)}],
    })
    return key


def test_manifest_is_empty_for_a_project_that_was_never_ingested(scenes_root):
    body = client.get("/api/model/manifest", params={"project_id": "never-ingested"}).json()
    assert body["files"] == {}
    assert body["axis_mapping"] == scene_export.AXIS_MAPPING


def test_manifest_lists_exported_scenes(scenes_root):
    key = _publish_scene()
    body = client.get("/api/model/manifest", params={"project_id": "demo"}).json()
    assert body["files"]["f1"]["scenes"][0]["scene_key"] == key


def test_scene_is_served_as_gltf_binary(scenes_root):
    key = _publish_scene()
    response = client.get(f"/api/model/scene/demo/f1/{key}.glb")
    assert response.status_code == 200
    assert response.headers["content-type"] == "model/gltf-binary"
    assert response.content == b"glTF-stub"


def test_scene_is_served_without_the_glb_suffix(scenes_root):
    """The manifest stores bare keys; both forms must resolve to one file."""
    key = _publish_scene()
    assert client.get(f"/api/model/scene/demo/f1/{key}").status_code == 200


@pytest.mark.parametrize("key", [
    "../../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd",
    "/etc/passwd",
    "..",
])
def test_scene_endpoint_refuses_path_traversal(scenes_root, key):
    """No crafted key may escape the scenes directory."""
    response = client.get(f"/api/model/scene/demo/f1/{key}")
    assert response.status_code in (400, 404)
    assert b"root:" not in response.content


def test_scene_endpoint_cannot_read_a_sibling_project(scenes_root):
    """Traversal via the project segment must fail too, not just the key."""
    _publish_scene(project="other", file_id="f1")
    key = scene_key("Level 5")
    assert client.get(f"/api/model/scene/..%2Fother/f1/{key}").status_code == 404


def test_missing_scene_reports_how_to_produce_it(scenes_root):
    response = client.get("/api/model/scene/demo/f1/abc123def456")
    assert response.status_code == 404
    assert "ingestion" in response.json()["detail"]


def test_elements_endpoint_reports_graph_unavailability_honestly(scenes_root):
    """A missing graph must not look like an empty building."""
    def refuse():
        raise RuntimeError("Couldn't connect to localhost:7687")

    original = routes.Neo4jClient
    routes.Neo4jClient = lambda: refuse()
    try:
        response = client.get("/api/model/elements", params={"project_id": "demo"})
    finally:
        routes.Neo4jClient = original
    assert response.status_code == 503
    assert response.json()["detail"] == "Building graph is unavailable."


class _RecordingClient:
    """Captures the query parameters instead of reaching a database."""

    last_parameters: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, parameters=None):
        type(self).last_parameters = dict(parameters or {})
        return [{
            "element_id": "demo::f1::guid-a", "ifc_guid": "guid-a", "name": "Wall A",
            "ifc_type": "IfcWall", "storey_name": "Level 5",
            "min_x": -1.0, "min_y": 2.0, "min_z": 3.0,
            "max_x": 4.0, "max_y": 5.0, "max_z": 6.0,
        }]


def _query_elements(scenes_root, monkeypatch, **params):
    monkeypatch.setattr(routes, "Neo4jClient", _RecordingClient)
    response = client.get("/api/model/elements", params={"project_id": "demo", **params})
    assert response.status_code == 200
    return response.json(), _RecordingClient.last_parameters


def test_elements_are_returned_in_the_viewer_coordinate_frame(scenes_root, monkeypatch):
    """The server owns the IFC->glTF axis mapping so the client never guesses."""
    body, _ = _query_elements(scenes_root, monkeypatch)
    element = body["elements"][0]
    assert element["min"] == pytest.approx([-1.0, 3.0, -5.0])
    assert element["max"] == pytest.approx([4.0, 6.0, -2.0])


def test_elements_carry_their_scene_key(scenes_root, monkeypatch):
    """Lets the client pick scenes without reimplementing key derivation."""
    body, _ = _query_elements(scenes_root, monkeypatch)
    assert body["elements"][0]["scene_key"] == scene_key("Level 5")


def test_no_scene_filter_means_the_whole_project(scenes_root, monkeypatch):
    _, parameters = _query_elements(scenes_root, monkeypatch)
    assert parameters["filter_storeys"] is False
    assert parameters["include_unassigned"] is False


def test_a_scene_key_restricts_the_query_to_its_storey(scenes_root, monkeypatch):
    key = _publish_scene()
    _, parameters = _query_elements(scenes_root, monkeypatch, scene_key=key)
    assert parameters["filter_storeys"] is True
    assert parameters["storey_names"] == ["Level 5"]
    assert parameters["include_unassigned"] is False


def test_the_unassigned_scene_selects_elements_that_have_no_storey(scenes_root, monkeypatch):
    """1,392 elements in the reference model have no storey and no name to match."""
    _publish_scene()
    _, parameters = _query_elements(scenes_root, monkeypatch, scene_key=UNASSIGNED_SCENE_KEY)
    assert parameters["filter_storeys"] is True
    assert parameters["storey_names"] == []
    assert parameters["include_unassigned"] is True


def test_an_unknown_scene_key_returns_nothing_rather_than_everything(scenes_root, monkeypatch):
    """A stale key must not silently widen to the whole building."""
    _, parameters = _query_elements(scenes_root, monkeypatch, scene_key="deadbeef0000")
    assert parameters["filter_storeys"] is True
    assert parameters["storey_names"] == []
    assert parameters["include_unassigned"] is False


def test_ifc_type_filter_is_passed_as_a_parameter_not_interpolated(scenes_root, monkeypatch):
    _, parameters = _query_elements(scenes_root, monkeypatch, ifc_type=["IfcStair", "IfcStairFlight"])
    assert parameters["ifc_types"] == ["IfcStair", "IfcStairFlight"]


def test_file_filter_is_passed_as_a_parameter(scenes_root, monkeypatch):
    _, parameters = _query_elements(
        scenes_root, monkeypatch, file_id=["architectural", "mep"],
    )
    assert parameters["file_ids"] == ["architectural", "mep"]


def test_scene_scope_ignores_files_outside_the_selected_models(scenes_root, monkeypatch):
    key = _publish_scene()
    _, parameters = _query_elements(
        scenes_root, monkeypatch, scene_key=key, file_id=["another-model"],
    )
    assert parameters["filter_storeys"] is True
    assert parameters["storey_names"] == []


def test_element_limit_is_bounded(scenes_root, monkeypatch):
    monkeypatch.setattr(routes, "Neo4jClient", _RecordingClient)
    assert client.get("/api/model/elements",
                      params={"project_id": "demo", "limit": 999999}).status_code == 422
    assert client.get("/api/model/elements",
                      params={"project_id": "demo", "limit": 0}).status_code == 422


def test_ask_request_accepts_a_project_scope():
    """The chat payload must carry project_id, or highlights cannot be scoped."""
    schema = client.get("/openapi.json").json()
    properties = schema["components"]["schemas"]["QuestionRequest"]["properties"]
    assert "project_id" in properties


def test_ingest_request_exposes_the_scene_export_switch():
    schema = client.get("/openapi.json").json()
    properties = schema["components"]["schemas"]["ProjectIngestRequest"]["properties"]
    assert properties["build_scenes"]["default"] is True


def test_viewer_module_is_served_and_resolvable_from_the_import_map():
    """three.js addons import the bare specifier "three"; the map must resolve it."""
    html = client.get("/").text
    import re
    import_map = json.loads(
        re.search(r'<script type="importmap">\s*(\{.*?\})\s*</script>', html, re.S).group(1)
    )
    assert client.get("/static/viewer.js").status_code == 200
    for specifier in ("three", "three/addons/"):
        assert specifier in import_map["imports"]
    assert client.get(import_map["imports"]["three"]).status_code == 200
    for addon in ("controls/OrbitControls.js", "loaders/GLTFLoader.js",
                  "utils/BufferGeometryUtils.js"):
        assert client.get(import_map["imports"]["three/addons/"] + addon).status_code == 200


# ----------------------------------------------------------------------
# /model/elements/by-guid — exact bounds for a known clash pair
# ----------------------------------------------------------------------

def _query_by_guid(scenes_root, monkeypatch, **params):
    monkeypatch.setattr(routes, "Neo4jClient", _RecordingClient)
    response = client.get(
        "/api/model/elements/by-guid",
        params={"project_id": "demo", "guid": ["guid-a"], **params},
    )
    return response, _RecordingClient.last_parameters


def test_by_guid_returns_viewer_frame_bounds_and_scene_key(scenes_root, monkeypatch):
    """Same element payload shape as /model/elements, so the viewer reuses it."""
    response, _ = _query_by_guid(scenes_root, monkeypatch)
    assert response.status_code == 200
    element = response.json()["elements"][0]
    assert element["ifc_guid"] == "guid-a"
    assert element["scene_key"] == scene_key("Level 5")
    assert element["min"] == pytest.approx([-1.0, 3.0, -5.0])


def test_by_guid_passes_guids_verbatim(scenes_root, monkeypatch):
    """IFC GlobalIds carry '$' and ':' characters; they are matched as property
    values, never sanitized like path segments."""
    guids = ["3DIIZQPe$4ndB2", "1Csh5jmV932w"]
    response, parameters = _query_by_guid(scenes_root, monkeypatch, guid=guids)
    assert response.status_code == 200
    assert parameters["guids"] == guids


def test_by_guid_applies_file_scope(scenes_root, monkeypatch):
    _, parameters = _query_by_guid(
        scenes_root, monkeypatch, file_id=["model-a", "model-b"],
    )
    assert parameters["file_ids"] == ["model-a", "model-b"]


def test_by_guid_requires_at_least_one_guid(scenes_root, monkeypatch):
    monkeypatch.setattr(routes, "Neo4jClient", _RecordingClient)
    # Omitting the parameter fails FastAPI validation; empty strings reach the
    # handler and get its explicit 400.
    assert client.get(
        "/api/model/elements/by-guid", params={"project_id": "demo"},
    ).status_code == 422
    assert client.get(
        "/api/model/elements/by-guid",
        params={"project_id": "demo", "guid": [""]},
    ).status_code == 400


def test_by_guid_reports_graph_unavailability_honestly(scenes_root):
    def refuse():
        raise RuntimeError("Couldn't connect to localhost:7687")

    original = routes.Neo4jClient
    routes.Neo4jClient = lambda: refuse()
    try:
        response = client.get(
            "/api/model/elements/by-guid",
            params={"project_id": "demo", "guid": ["guid-a"]},
        )
    finally:
        routes.Neo4jClient = original
    assert response.status_code == 503
    assert response.json()["detail"] == "Building graph is unavailable."


# ----------------------------------------------------------------------
# Clash results endpoints: 503 on graph failure, storey fields present
# ----------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/clashes", "/api/violations", "/api/issues"])
def test_issue_endpoints_report_graph_unavailability_honestly(path):
    """An infrastructure failure must not read as "no clashes found" ([])."""
    def refuse():
        raise RuntimeError("Couldn't connect to localhost:7687")

    original = routes.Neo4jClient
    routes.Neo4jClient = lambda: refuse()
    try:
        response = client.get(path, params={"project_id": "demo"})
    finally:
        routes.Neo4jClient = original
    assert response.status_code == 503
    assert response.json()["detail"] == "Building graph is unavailable."


def test_issue_query_projects_storey_names_and_aliases(monkeypatch):
    """The clash card shows each element's storey; the query must project it and
    expose it under the additive element_*_storey alias."""

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, query, params=None):
            assert "a.storeyName AS a_storey_name" in query
            assert "b.storeyName AS b_storey_name" in query
            return [{
                "a_id": "p::a::guid-a", "a_guid": "guid-a", "a_name": "Beam",
                "a_type": "IfcBeam", "a_storey_name": "Level 5",
                "a_source_ifc_file": "structure.ifc", "a_source_file_id": "a",
                "a_discipline": "structure",
                "b_id": "p::a::guid-b", "b_guid": "guid-b", "b_name": "Wall",
                "b_type": "IfcWall", "b_storey_name": "Level 5",
                "b_source_ifc_file": "structure.ifc", "b_source_file_id": "a",
                "b_discipline": "structure",
                "issue": "CLASH", "metric": 1.5, "project_id": "p",
                "cross_file": False,
                "anomaly_score_a": None, "anomaly_score_b": None,
                "combined_anomaly_score": None,
            }]

    monkeypatch.setattr(routes, "Neo4jClient", Client)
    rows = routes._run_issue_query("CLASH", None, None, "p", ["a"])
    row = rows[0]
    assert row["a_storey_name"] == "Level 5" and row["b_storey_name"] == "Level 5"
    assert row["element_a_storey"] == "Level 5"
    assert row["element_b_storey"] == "Level 5"
    # The legacy a_*/b_* contract is unchanged.
    assert row["a_type"] == "IfcBeam" and row["element_a_type"] == "IfcBeam"
