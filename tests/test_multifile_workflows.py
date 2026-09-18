from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

import extract_graph
from bim_graph import clash_pipeline, project_registry
from bim_graph.coordinate_system import validate_federation
from rag import embedder
from main import app
from api import routes
from fastapi.testclient import TestClient


def test_empty_ifc_filters_mean_all():
    for selection in (None, [], [""], ["  "]):
        assert extract_graph._normalize_optional_filter(selection) is None


def test_nonempty_ifc_filter_remains_a_trimmed_subset():
    assert extract_graph._normalize_optional_filter([" Level 6 ", "IfcWall"]) == ["Level 6", "IfcWall"]


def coordinate_record(
    name,
    *,
    scale=1.0,
    project="project-guid",
    context=None,
    project_name=None,
    building_name=None,
    placement=None,
):
    identity = placement or [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    return {
        "filename": name,
        "coordinate_system": {
            "unit_scale_to_metre": scale,
            "project_guids": [project] if project else [],
            "site_guids": [],
            "projects": ([{"guid": project, "name": project_name, "long_name": None}]
                         if project_name else []),
            "sites": [{"guid": None, "name": "Site", "long_name": None,
                       "placement": identity, "georef": None}],
            "buildings": ([{"guid": None, "name": building_name, "long_name": None,
                            "placement": identity}] if building_name else []),
            "contexts": context if context is not None else [{"wcs": [[1, 0], [0, 1]], "true_north": [0, 1]}],
            "map_conversions": [],
        },
    }


def test_coordinate_validation_accepts_verified_shared_frame():
    result = validate_federation([coordinate_record("arch.ifc"), coordinate_record("mep.ifc")])
    assert result["compatible"] is True
    assert result["basis"] == "shared_project_guid"


def test_coordinate_validation_accepts_shared_site_guid_when_project_guids_differ():
    architecture = coordinate_record("arch.ifc", project="arch-project")
    structure = coordinate_record("structure.ifc", project="structure-project")
    architecture["coordinate_system"]["site_guids"] = ["shared-site"]
    structure["coordinate_system"]["site_guids"] = ["shared-site"]

    result = validate_federation([architecture, structure])

    assert result["compatible"] is True
    assert result["basis"] == "shared_site_guid"


def test_coordinate_validation_accepts_matching_explicit_map_conversion():
    architecture = coordinate_record("arch.ifc", project="arch-project")
    structure = coordinate_record("structure.ifc", project="structure-project")
    conversion = {
        "Eastings": 500000.0, "Northings": 4000000.0,
        "OrthogonalHeight": 12.0, "XAxisAbscissa": 1.0,
        "XAxisOrdinate": 0.0, "Scale": 1.0,
    }
    architecture["coordinate_system"]["map_conversions"] = [conversion]
    structure["coordinate_system"]["map_conversions"] = [conversion]

    result = validate_federation([architecture, structure])

    assert result["compatible"] is True
    assert result["basis"] == "shared_map_conversion"
    assert result["coordinate_reference"] == "shared_map_conversion"


def test_coordinate_validation_rejects_unit_mismatch_and_unknown_alignment():
    units = validate_federation([
        coordinate_record("feet.ifc", scale=0.3048), coordinate_record("mm.ifc", scale=0.001),
    ])
    unknown = validate_federation([
        coordinate_record("a.ifc", project="a"), coordinate_record("b.ifc", project="b"),
    ])
    assert units["compatible"] is False and units["status"] == "unit_mismatch"
    assert unknown["compatible"] is False and unknown["status"] == "unverified_alignment"


def test_coordinate_validation_rejects_missing_metadata():
    result = validate_federation([coordinate_record("ready.ifc"), {"filename": "old.ifc"}])
    assert result["compatible"] is False
    assert result["status"] == "missing_coordinate_metadata"
    assert result["files"] == ["old.ifc"]


def test_coordinate_validation_accepts_same_named_building_across_discipline_exports():
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    architecture = coordinate_record(
        "model_0_arc.ifc", project="architecture-guid",
        project_name="Project_0", building_name="Building_0",
        context=[{"wcs": identity, "true_north": [0, 1]}] * 5,
    )
    structure = coordinate_record(
        "model_0_structure.ifc", project="structure-guid",
        project_name="Project_0", building_name="Building_0",
        context=[{"wcs": identity, "true_north": None}],
    )

    result = validate_federation([architecture, structure])

    assert result["compatible"] is True
    assert result["basis"] == "shared_project_and_building_identity"


def test_coordinate_validation_does_not_trust_matching_world_origins_alone():
    first = coordinate_record(
        "building-a.ifc", project="a", project_name="Project A", building_name="Building A",
    )
    second = coordinate_record(
        "building-b.ifc", project="b", project_name="Project B", building_name="Building B",
    )

    result = validate_federation([first, second])

    assert result["compatible"] is False
    assert result["status"] == "unverified_alignment"


def test_coordinate_validation_does_not_trust_shared_placeholder_georeference():
    first = coordinate_record(
        "building-a.ifc", project="a", project_name="Project A", building_name="Building A",
    )
    second = coordinate_record(
        "building-b.ifc", project="b", project_name="Project B", building_name="Building B",
    )
    placeholder = {"latitude": [0, 0, 0], "longitude": [0, 0, 0], "elevation": 0}
    first["coordinate_system"]["sites"][0]["georef"] = placeholder
    second["coordinate_system"]["sites"][0]["georef"] = placeholder

    result = validate_federation([first, second])

    assert result["compatible"] is False
    assert result["status"] == "unverified_alignment"


def test_coordinate_validation_does_not_trust_generic_project_and_building_names():
    first = coordinate_record(
        "arch.ifc", project="a", project_name="Project", building_name="Building",
    )
    second = coordinate_record(
        "structure.ifc", project="b", project_name="Project", building_name="Building",
    )

    result = validate_federation([first, second])

    assert result["compatible"] is False
    assert result["status"] == "unverified_alignment"


def test_coordinate_validation_warns_on_georef_conflict_when_local_identity_is_verified():
    first = coordinate_record(
        "arch.ifc", project="a", project_name="Campus", building_name="Tower",
    )
    second = coordinate_record(
        "structure.ifc", project="b", project_name="Campus", building_name="Tower",
    )
    first["coordinate_system"]["sites"][0]["georef"] = {
        "latitude": [10, 0, 0], "longitude": [20, 0, 0], "elevation": 0,
    }
    second["coordinate_system"]["sites"][0]["georef"] = {
        "latitude": [30, 0, 0], "longitude": [40, 0, 0], "elevation": 0,
    }

    result = validate_federation([first, second])

    assert result["compatible"] is True
    assert result["basis"] == "shared_project_and_building_identity"
    assert result["warnings"]


def test_coordinate_validation_rejects_same_named_building_with_transform_mismatch():
    shifted = [[1, 0, 0, 25], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    first = coordinate_record(
        "arch.ifc", project="a", project_name="Campus", building_name="Tower",
    )
    second = coordinate_record(
        "structure.ifc", project="b", project_name="Campus", building_name="Tower",
        context=[{"wcs": shifted, "true_north": [0, 1]}], placement=shifted,
    )

    result = validate_federation([first, second])

    assert result["compatible"] is False
    assert result["status"] == "coordinate_frame_mismatch"


def test_coordinate_validation_rejects_ambiguous_contexts_inside_one_file():
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    shifted = [[1, 0, 0, 50], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    ambiguous = coordinate_record(
        "arch.ifc", project_name="Campus", building_name="Tower",
        context=[{"wcs": identity, "true_north": [0, 1]},
                 {"wcs": shifted, "true_north": [0, 1]}],
    )
    structure = coordinate_record(
        "structure.ifc", project_name="Campus", building_name="Tower",
        context=[{"wcs": identity, "true_north": [0, 1]}],
    )

    result = validate_federation([ambiguous, structure])

    assert result["compatible"] is False
    assert result["status"] == "ambiguous_coordinate_frame"


def test_coordinate_validation_rejects_nonfinite_or_nonpositive_units():
    for scale in (0, -1, float("nan")):
        result = validate_federation([
            coordinate_record("a.ifc", scale=scale),
            coordinate_record("b.ifc", scale=scale),
        ])
        assert result["compatible"] is False
        assert result["status"] == "invalid_coordinate_metadata"


def test_coordinate_validation_fails_closed_on_malformed_nested_metadata():
    malformed = coordinate_record("bad.ifc")
    malformed["coordinate_system"].update({
        "project_guids": None,
        "site_guids": "not-a-list",
        "projects": "not-a-list",
        "sites": None,
        "buildings": [None, "bad"],
        "contexts": None,
        "map_conversions": None,
    })

    result = validate_federation([malformed, coordinate_record("good.ifc")])

    assert result["compatible"] is False
    assert result["status"] == "coordinate_frame_mismatch"


def test_cross_file_clash_preserves_both_sources_and_guids():
    frame = pd.DataFrame([
        {"id": "p::arch::wall", "ifc_guid": "wall", "type": "IfcWall", "name": "Wall",
         "source_ifc_file": "architecture.ifc", "source_file_id": "arch", "discipline": "Architecture",
         "project_id": "p", "storey_name": "Level 1", "min_x": 0, "min_y": 0, "min_z": 0,
         "max_x": 2, "max_y": 1, "max_z": 3},
        {"id": "p::mep::pipe", "ifc_guid": "pipe", "type": "IfcFlowSegment", "name": "Pipe",
         "source_ifc_file": "mep.ifc", "source_file_id": "mep", "discipline": "MEP",
         "project_id": "p", "storey_name": "MEP Level", "min_x": 1, "min_y": 0.5, "min_z": 1,
         "max_x": 3, "max_y": 0.8, "max_z": 1.2},
    ])
    issues = clash_pipeline.detect(frame)
    assert len(issues) == 1
    assert issues[0]["cross_file"] is True
    assert issues[0]["a_ifc_guid"] == "wall"
    assert {issues[0]["a_source_ifc_file"], issues[0]["b_source_ifc_file"]} == {
        "architecture.ifc", "mep.ifc",
    }


def test_duplicate_export_of_same_guid_is_not_a_cross_file_clash():
    rows = []
    for file_id in ("arch", "coordination"):
        rows.append({
            "id": f"p::{file_id}::same", "ifc_guid": "same", "type": "IfcBeam",
            "source_file_id": file_id, "source_ifc_file": f"{file_id}.ifc", "project_id": "p",
            "min_x": 0, "min_y": 0, "min_z": 0, "max_x": 1, "max_y": 1, "max_z": 1,
        })
    assert clash_pipeline.detect(pd.DataFrame(rows)) == []


def test_graph_csv_schema_keeps_original_guid_and_project_provenance(tmp_path):
    nodes_path, edges_path = tmp_path / "nodes.csv", tmp_path / "edges.csv"
    row = {
        "id": "p::f::guid", "ifc_guid": "guid", "type": "IfcBeam", "name": "B",
        "storey_id": "", "storey_name": "L1", "min_x": 0, "min_y": 0, "min_z": 0,
        "max_x": 1, "max_y": 1, "max_z": 1, "source_ifc_file": "structure.ifc",
        "source_file_id": "f", "discipline": "Structure", "project_id": "p",
        "coordinate_system_id": "coords",
    }
    extract_graph.write_csvs([row], [], nodes_path, edges_path)
    with nodes_path.open(encoding="utf-8") as handle:
        saved = next(csv.DictReader(handle))
    assert saved["id"] == "p::f::guid"
    assert saved["ifc_guid"] == "guid"
    assert saved["source_ifc_file"] == "structure.ifc"
    assert saved["project_id"] == "p"


def test_multi_extraction_keeps_success_when_sibling_file_fails(monkeypatch, tmp_path):
    records = [
        {"project_id": "p", "file_id": "good", "filename": "good.ifc", "stored_path": "good",
         "coordinate_system": {"coordinate_fingerprint": "c"}},
        {"project_id": "p", "file_id": "bad", "filename": "bad.ifc", "stored_path": "bad",
         "coordinate_system": {"coordinate_fingerprint": "c"}},
    ]
    monkeypatch.setattr(extract_graph.ifcopenshell, "open", lambda path: path)
    monkeypatch.setattr(extract_graph, "extract", lambda model, **kwargs: (
        ([{"id": "good", "ifc_guid": "good", "type": "IfcWall", "name": "", "storey_id": "",
           "storey_name": "", "min_x": "", "min_y": "", "min_z": "", "max_x": "", "max_y": "",
           "max_z": "", "source_ifc_file": "good.ifc", "source_file_id": "good", "discipline": "unspecified",
           "project_id": "p", "coordinate_system_id": "c"}], [])
        if model == "good" else (_ for _ in ()).throw(RuntimeError("broken geometry"))
    ))
    nodes, edges, results = extract_graph.run_multi_extraction(
        records, tmp_path / "nodes.csv", tmp_path / "edges.csv"
    )
    assert len(nodes) == 1 and edges == []
    assert [item["status"] for item in results] == ["processed", "failed"]


def test_project_registry_isolates_projects_and_records_status(monkeypatch, tmp_path):
    monkeypatch.setattr(project_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    model = tmp_path / "model.ifc"
    model.write_bytes(b"IFC")
    first = project_registry.register_uploaded_file("building-a", model, "architecture.ifc")
    project_registry.update_file("building-a", first["file_id"], status="ingested", processing_status="imported")
    project_registry.register_uploaded_file("building-b", model, "other.ifc")
    projects = project_registry.list_projects()
    assert {item["project_id"] for item in projects} == {"building-a", "building-b"}
    assert project_registry.get_files("building-a")[0]["status"] == "ingested"


def test_vector_status_groups_chunks_into_source_documents(monkeypatch):
    monkeypatch.setattr(embedder, "get_all_chunks", lambda settings=None: [
        {"id": "a1", "document": "x", "metadata": {"document_id": "a", "source": "a.pdf", "page_number": 1}},
        {"id": "a2", "document": "y", "metadata": {"document_id": "a", "source": "a.pdf", "page_number": 2}},
        {"id": "b1", "document": "z", "metadata": {"document_id": "b", "source": "b.pdf", "page_number": 1}},
    ])
    documents = embedder.list_indexed_documents()
    assert [(item["filename"], item["chunk_count"]) for item in documents] == [("a.pdf", 2), ("b.pdf", 1)]


def test_frontend_inputs_are_multi_file_and_send_batch_fields():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    js = Path("static/app.js").read_text(encoding="utf-8")
    assert 'id="ifc-file" accept=".ifc" class="hidden-input" multiple' in html
    assert 'id="pdf-file" name="files" accept=".pdf" class="hidden-input" multiple' in html
    assert 'formData.append("files", item)' in js
    assert 'formData.append("file", files[0])' in js
    assert "selectedIfcFileIds" in js


def test_rag_upload_accepts_single_and_multiple_files_under_plural_field(monkeypatch):
    monkeypatch.setattr(routes, "_ensure_rag", lambda: None)
    monkeypatch.setattr(
        routes,
        "_RAG_CHUNKER",
        lambda _path, doc_id, source, **_metadata: [{"id": f"{doc_id}-1", "source": source}],
    )
    monkeypatch.setattr(routes, "_RAG_EMBEDDER", {
        "delete_document": lambda _doc_id: 0,
        "embed_and_store": lambda chunks: len(chunks),
        "CHROMA_DIR": "test-chroma",
        "COLLECTION_NAME": "test-collection",
    })

    response = TestClient(app).post(
        "/api/rag/upload",
        files=[("files", ("regulation.pdf", b"minimal pdf test", "application/pdf"))],
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1
    assert response.json()["files"][0]["filename"] == "regulation.pdf"

    batch_response = TestClient(app).post(
        "/api/rag/upload",
        files=[
            ("files", ("first.pdf", b"first pdf", "application/pdf")),
            ("files", ("second.pdf", b"second pdf", "application/pdf")),
        ],
    )

    assert batch_response.status_code == 200
    assert batch_response.json()["succeeded"] == 2
    assert [item["filename"] for item in batch_response.json()["files"]] == [
        "first.pdf", "second.pdf",
    ]


def test_upload_validation_error_is_json_serializable():
    response = TestClient(app).post(
        "/api/rag/upload",
        data={"files": "this is not an uploaded file"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == (
        "The 'files' and 'file' fields must contain uploaded files."
    )


def test_openapi_exposes_batch_and_legacy_upload_fields():
    schema = app.openapi()
    components = schema["components"]["schemas"]
    for path in ("/api/ifc/upload", "/api/rag/upload"):
        body = schema["paths"][path]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
        if "$ref" in body:
            body = components[body["$ref"].rsplit("/", 1)[-1]]
        files_schema = body["properties"]["files"]
        if "anyOf" in files_schema:
            files_schema = files_schema["anyOf"][0]
        assert files_schema["type"] == "array"
        assert "file" in body["properties"]  # backward-compatible single-file part


def test_rule_only_result_query_does_not_warn_for_absent_anomaly_properties(monkeypatch):
    captured = {}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, query, parameters=None):
            captured.update(query=query, parameters=parameters)
            return []

    monkeypatch.setattr(routes, "Neo4jClient", FakeClient)
    assert routes._run_issue_query("CLASH", None, None, "project") == []
    assert "r.anomalyScoreA AS anomaly_score_a" not in captured["query"]
    assert "r[$anomaly_score_a_property] AS anomaly_score_a" in captured["query"]
    assert captured["parameters"]["anomaly_score_a_property"] == "anomalyScoreA"
