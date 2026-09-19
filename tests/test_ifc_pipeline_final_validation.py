"""Release-regression coverage for IFC scope, reset, results, and clearance behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import extract_graph
from api import routes
from bim_graph import clash_pipeline, load_to_neo4j, project_registry, scene_export
from bim_graph.coordinate_system import inspect_coordinate_system, validate_federation


def _element(
    element_id: str,
    file_id: str,
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
    *,
    ifc_type: str = "IfcBeam",
    guid: str | None = None,
) -> dict:
    return {
        "id": element_id,
        "ifc_guid": guid or element_id,
        "type": ifc_type,
        "name": element_id,
        "source_ifc_file": f"{file_id}.ifc",
        "source_file_id": file_id,
        "discipline": file_id,
        "project_id": "project",
        "storey_name": "Level 1",
        "min_x": minimum[0], "min_y": minimum[1], "min_z": minimum[2],
        "max_x": maximum[0], "max_y": maximum[1], "max_z": maximum[2],
    }


def test_aabb_contract_threshold_touching_rounding_and_sweep_scope():
    clash = _element("clash", "a", (0, 0, 0), (2, 2, 2))
    overlap = _element("overlap", "b", (1, 1, 1), (3, 3, 3))
    touching = _element("touch", "b", (2, 0, 0), (3, 1, 1))
    near = _element("near", "b", (2.123456, 0, 0), (3, 1, 1))
    threshold = _element("threshold", "b", (2.25, 0, 0), (3, 1, 1))
    far = _element("far", "c", (100, 100, 100), (101, 101, 101))

    assert clash_pipeline.classify_pair(clash, overlap) == ("CLASH", 1)
    assert clash_pipeline.classify_pair(clash, touching) == ("CLEARANCE_VIOLATION", 0.0)
    assert clash_pipeline.classify_pair(clash, threshold)[0] is None

    issues = clash_pipeline.detect(pd.DataFrame([clash, overlap, touching, near, threshold, far]))
    assert not any("far" in {row["a_id"], row["b_id"]} for row in issues)
    near_issue = next(row for row in issues if "near" in {row["a_id"], row["b_id"]})
    assert near_issue["metric"] == 0.1235


def test_ignore_pairs_and_duplicate_guids_remain_excluded():
    door = _element("door", "a", (0, 0, 0), (2, 2, 2), ifc_type="IfcDoor")
    wall = _element("wall", "a", (1, 1, 1), (3, 3, 3), ifc_type="IfcWallStandardCase")
    duplicate_a = _element("duplicate-a", "a", (0, 0, 0), (2, 2, 2), guid="same")
    duplicate_b = _element("duplicate-b", "b", (1, 1, 1), (3, 3, 3), guid="same")

    assert clash_pipeline.detect(pd.DataFrame([door, wall])) == []
    assert clash_pipeline.detect(pd.DataFrame([duplicate_a, duplicate_b])) == []


def test_issue_provenance_includes_both_file_ids_names_disciplines_and_guids():
    first = _element("a-id", "architecture", (0, 0, 0), (2, 2, 2), guid="a-guid")
    second = _element("b-id", "structure", (1, 1, 1), (3, 3, 3), guid="b-guid")

    issue = clash_pipeline.detect(pd.DataFrame([first, second]))[0]

    assert issue["project_id"] == "project"
    assert issue["a_source_file_id"] == "architecture"
    assert issue["b_source_file_id"] == "structure"
    assert issue["a_source_ifc_file"] == "architecture.ifc"
    assert issue["b_source_ifc_file"] == "structure.ifc"
    assert issue["a_discipline"] == "architecture"
    assert issue["b_discipline"] == "structure"
    assert {issue["a_ifc_guid"], issue["b_ifc_guid"]} == {"a-guid", "b-guid"}
    assert issue["cross_file"] is True
    assert "a.sourceFileId AS a_source_file_id" in clash_pipeline.LIST_ISSUES_QUERY
    assert "b.discipline AS b_discipline" in clash_pipeline.LIST_ISSUES_QUERY


def test_clearance_is_explicitly_si_and_ifcopenshell_conversion_is_pinned():
    assert extract_graph.geom_settings.get("convert-back-units") is False
    assert clash_pipeline.GEOMETRY_UNIT_SCALE_TO_METRE == 1.0
    assert clash_pipeline.CLEARANCE_THRESHOLD_METRES == pytest.approx(0.25)
    assert clash_pipeline.CLEARANCE_THRESHOLD == pytest.approx(0.25)


def test_geometry_worker_count_is_bounded_and_configurable(monkeypatch):
    monkeypatch.setattr(extract_graph.multiprocessing, "cpu_count", lambda: 32)
    monkeypatch.delenv("BIM_GEOMETRY_WORKERS", raising=False)
    assert extract_graph._geometry_worker_count() == 4
    monkeypatch.setenv("BIM_GEOMETRY_WORKERS", "2")
    assert extract_graph._geometry_worker_count() == 2
    monkeypatch.setenv("BIM_GEOMETRY_WORKERS", "999")
    assert extract_graph._geometry_worker_count() == 32
    monkeypatch.setenv("BIM_GEOMETRY_WORKERS", "invalid")
    assert extract_graph._geometry_worker_count() == 4


@pytest.mark.skipif(
    not all(Path(path).exists() for path in (
        "dataset/ifc/2026_BIMprojects/model_0_arc.ifc",
        "dataset/ifc/2026_BIMprojects/model_0_structure.ifc",
    )),
    reason="real model-0 discipline exports are unavailable",
)
def test_real_model_0_architecture_and_structure_federate_despite_distinct_guids():
    paths = [
        Path("dataset/ifc/2026_BIMprojects/model_0_arc.ifc"),
        Path("dataset/ifc/2026_BIMprojects/model_0_structure.ifc"),
    ]
    records = [
        {"filename": path.name, "coordinate_system": inspect_coordinate_system(path)}
        for path in paths
    ]

    assert records[0]["coordinate_system"]["project_guids"] != records[1]["coordinate_system"]["project_guids"]
    assert records[0]["coordinate_system"]["site_guids"] != records[1]["coordinate_system"]["site_guids"]
    result = validate_federation(records)
    assert result["compatible"] is True
    assert result["basis"] == "shared_project_and_building_identity"


@pytest.mark.skipif(
    not Path("dataset/ifc/2026_BIMprojects/model_1_structure.ifc").exists(),
    reason="millimetre-authored real IFC fixture is unavailable",
)
def test_real_millimetre_ifc_geometry_is_normalized_to_metres():
    import ifcopenshell
    import ifcopenshell.geom as geom
    import ifcopenshell.util.unit as unit

    model = ifcopenshell.open("dataset/ifc/2026_BIMprojects/model_1_structure.ifc")
    assert unit.calculate_unit_scale(model) == pytest.approx(0.001)
    element = model.by_type("IfcWall")[0]
    shape = geom.create_shape(extract_graph.geom_settings, element)
    vertices = shape.geometry.verts
    # A source-unit leak would make this ordinary wall thousands of coordinate
    # units wide/high. SI output stays in a plausible building-scale envelope.
    assert max(abs(value) for value in vertices) < 100


def test_scoped_cleanup_removes_issues_touching_any_selected_file():
    query = " ".join(clash_pipeline.CLEAR_SCOPED_ISSUES_QUERY.split())
    assert "a.sourceFileId IN $file_ids OR b.sourceFileId IN $file_ids" in query
    assert "a.sourceFileId IN $file_ids AND b.sourceFileId IN $file_ids" not in query
    assert "r.projectId IS NULL AND a.projectId = $project_id AND b.projectId = $project_id" in query


class _ClashClient:
    calls: list[tuple[str, object]] = []

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def verify_connectivity(self): return None

    def run(self, query, params=None):
        type(self).calls.append((query, params))
        if query == clash_pipeline.FETCH_QUERY:
            return []
        if "RETURN r.issue AS issue, count(*) AS n" in query:
            return []
        return []

    def run_batched(self, query, rows, batch_size):
        type(self).calls.append((query, list(rows)))


def test_zero_issue_rerun_still_executes_scoped_cleanup(monkeypatch):
    _ClashClient.calls = []
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", _ClashClient)

    summary = clash_pipeline.run_clash_detection(project_id="p", file_ids=["a", "b"])

    assert summary["issues_detected"] == 0
    cleanup = next(params for query, params in _ClashClient.calls
                   if query == clash_pipeline.CLEAR_SCOPED_ISSUES_QUERY)
    assert cleanup == {"project_id": "p", "file_ids": ["a", "b"]}


def test_legacy_unscoped_rerun_also_clears_stale_issues(monkeypatch):
    _ClashClient.calls = []
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", _ClashClient)

    clash_pipeline.run_clash_detection()

    cleanup = next(params for query, params in _ClashClient.calls
                   if query == clash_pipeline.CLEAR_SCOPED_ISSUES_QUERY)
    assert cleanup == {"project_id": None, "file_ids": []}


class _ScopedClashClient:
    elements = [
        _element("a-one", "a", (0, 0, 0), (2, 2, 2)),
        _element("a-two", "a", (1, 1, 1), (3, 3, 3)),
        _element("b-one", "b", (1.5, 1.5, 1.5), (3.5, 3.5, 3.5)),
        _element("c-one", "c", (1.75, 1.75, 1.75), (3.75, 3.75, 3.75)),
    ]
    written: list[dict] = []

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def verify_connectivity(self): return None

    def run(self, query, params=None):
        if query == clash_pipeline.FETCH_QUERY:
            selected = set((params or {}).get("file_ids") or [])
            return [row for row in self.elements
                    if not selected or row["source_file_id"] in selected]
        if "RETURN r.issue AS issue, count(*) AS n" in query:
            return []
        return []

    def run_batched(self, query, rows, batch_size):
        if query == clash_pipeline.WRITE_CLASH_QUERY:
            type(self).written.extend(rows)


@pytest.mark.parametrize(
    ("selected", "expected_files", "expects_cross_file"),
    [
        (["a"], {"a"}, False),
        (["a", "b"], {"a", "b"}, True),
        (["a", "b", "c"], {"a", "b", "c"}, True),
    ],
)
def test_analysis_uses_only_selected_single_two_or_all_file_scope(
    monkeypatch, selected, expected_files, expects_cross_file,
):
    _ScopedClashClient.written = []
    monkeypatch.setattr(clash_pipeline, "Neo4jClient", _ScopedClashClient)

    summary = clash_pipeline.run_clash_detection(project_id="project", file_ids=selected)

    participating = {
        file_id
        for row in _ScopedClashClient.written
        for file_id in (row["a_source_file_id"], row["b_source_file_id"])
    }
    assert participating == expected_files
    assert any(row["cross_file"] for row in _ScopedClashClient.written) is expects_cross_file
    assert summary["elements_checked"] == sum(
        row["source_file_id"] in expected_files for row in _ScopedClashClient.elements
    )


def test_registry_global_reset_marks_every_project_out_of_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(project_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(project_registry, "PROJECT_ROOT", tmp_path / "projects")
    source = tmp_path / "source.ifc"
    source.write_bytes(b"IFC")
    for project_id in ("project-a", "project-b"):
        record = project_registry.register_uploaded_file(project_id, source, f"{project_id}.ifc")
        project_registry.update_file(
            project_id, record["file_id"], status="ingested", processing_status="analyzed",
        )

    assert project_registry.mark_all_files_not_in_graph() == 2
    for project_id in ("project-a", "project-b"):
        record = project_registry.get_files(project_id)[0]
        assert record["status"] == "uploaded"
        assert record["processing_status"] == "not_in_graph"


def test_scene_reconciliation_after_wipe_removes_unrelated_generated_models(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_export, "SCENES_ROOT", tmp_path / "scenes")
    for project_id, file_id in (("keep-project", "keep-file"), ("old-project", "old-file")):
        scene_export.merge_manifest(project_id, {
            "file_id": file_id, "source_ifc_file": f"{file_id}.ifc", "scenes": [],
        })
        directory = scene_export.scene_directory(project_id, file_id)
        directory.mkdir(parents=True)
        (directory / "scene.glb").write_bytes(b"generated")

    scene_export.reconcile_scene_scope({"keep-project": ["keep-file"]})

    assert scene_export.scene_directory("keep-project", "keep-file").exists()
    assert not scene_export.scene_directory("old-project", "old-file").exists()
    assert scene_export.read_manifest("old-project")["files"] == {}


class _IssueClient:
    query = ""
    params: dict = {}

    def __enter__(self): return self
    def __exit__(self, *_args): return None

    def run(self, query, params=None):
        type(self).query = query
        type(self).params = dict(params or {})
        return [{
            "a_id": "p::a::one", "a_guid": "one", "a_type": "IfcBeam", "a_name": "A",
            "a_source_ifc_file": "a.ifc", "a_source_file_id": "a", "a_discipline": "structure",
            "b_id": "p::b::two", "b_guid": "two", "b_type": "IfcWall", "b_name": "B",
            "b_source_ifc_file": "b.ifc", "b_source_file_id": "b", "b_discipline": "architecture",
            "issue": "CLASH", "metric": 1.0, "project_id": "p", "cross_file": True,
            "anomaly_score_a": None, "anomaly_score_b": None, "combined_anomaly_score": None,
        }]


def test_results_api_query_is_project_and_selected_file_scoped(monkeypatch):
    monkeypatch.setattr(routes, "Neo4jClient", _IssueClient)

    rows = routes._run_issue_query(None, None, None, "p", ["a", "b"])

    assert _IssueClient.params["project_id"] == "p"
    assert _IssueClient.params["file_ids"] == ["a", "b"]
    assert "a.sourceFileId IN $file_ids AND b.sourceFileId IN $file_ids" in _IssueClient.query
    assert rows[0]["a_source_file_id"] == "a"
    assert rows[0]["b_source_file_id"] == "b"
    assert rows[0]["element_a_id"] == "p::a::one"
    assert rows[0]["element_a_guid"] == "one"
    assert rows[0]["element_a_source_file"] == "a.ifc"
    assert rows[0]["element_a_discipline"] == "structure"
    assert rows[0]["element_b_id"] == "p::b::two"
    assert rows[0]["element_b_guid"] == "two"
    assert rows[0]["relation_scope"] == "CROSS-FILE"


def test_frontend_results_scope_and_stale_response_guard_are_present():
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    template = Path("templates/index.html").read_text(encoding="utf-8")
    translations = Path("static/i18n.js").read_text(encoding="utf-8")
    assert "reset_all: ingestForm.querySelector('input[name=\"reset\"]')" in javascript
    assert 'url.searchParams.append("file_id", fileId)' in javascript
    assert "const loadToken = ++resultsLoadToken" in javascript
    assert "if (loadToken !== resultsLoadToken) return" in javascript
    assert "scopeKey !== currentResultScopeKey()" in javascript
    assert 'if (target === "results")' in javascript and "loadResults();" in javascript
    assert 'id="resultTypeFilter"' in template
    assert 'data-i18n="results.allTypes">ALL</span>' in template
    assert "Empty means all types." not in template
    assert "Leave empty to include all types." not in template
    assert '"results.allTypes": "ALL"' in translations
    assert "Empty means all types." not in translations
    assert "Leave empty to include all types." not in translations
    assert "خالی یعنی همه انواع" not in translations
    assert 'input type="checkbox" data-select-all' in javascript
    assert "if (allCheckbox.checked) this.selectAll()" in javascript
    assert "else this.clear()" in javascript
    assert "currentTypesFilter = resultTypeDropdown.getFilterValue() || []" in javascript
    assert '/static/i18n.js?v=9' in template
    assert '/static/app.js?v=17' in template


def test_project_ingest_reset_all_reconciles_registry_and_scene_scope(monkeypatch):
    project_id = "project"
    records = {
        "a": {
            "file_id": "a", "project_id": project_id, "filename": "a.ifc",
            "status": "ingested", "processing_status": "analyzed",
            "coordinate_system": {"unit_scale_to_metre": 1.0},
        },
        "b": {
            "file_id": "b", "project_id": project_id, "filename": "b.ifc",
            "status": "ingested", "processing_status": "analyzed",
            "coordinate_system": {"unit_scale_to_metre": 1.0},
        },
    }
    calls = {}

    def fake_get_files(_project_id, file_ids=None):
        values = list(records.values())
        return [item.copy() for item in values if not file_ids or item["file_id"] in file_ids]

    def fake_update(_project_id, file_id, **updates):
        records[file_id].update(updates)
        return records[file_id].copy()

    def fake_mark_all():
        for record in records.values():
            record.update(status="uploaded", processing_status="not_in_graph")
        calls["marked"] = True
        return 2

    def fake_load(**kwargs):
        calls["load"] = kwargs
        return {"reset": kwargs["reset"]}

    monkeypatch.setattr(routes, "get_files", fake_get_files)
    monkeypatch.setattr(routes, "update_file", fake_update)
    monkeypatch.setattr(routes, "validate_federation", lambda _records: {"compatible": True})
    monkeypatch.setattr(routes, "run_multi_extraction", lambda *_args, **_kwargs: (
        [{"id": "project::a::guid"}], [],
        [{"file_id": "a", "filename": "a.ifc", "status": "processed", "nodes": 1, "edges": 0}],
    ))
    monkeypatch.setattr(routes.load_to_neo4j, "load", fake_load)
    monkeypatch.setattr(routes, "mark_all_files_not_in_graph", fake_mark_all)
    monkeypatch.setattr(routes, "reconcile_scene_scope", lambda keep: calls.update(reconciled=keep))
    monkeypatch.setattr(routes, "read_manifest", lambda _project: {"files": {"a": {}}})

    response = routes.ingest_ifc_project(
        project_id,
        routes.ProjectIngestRequest(
            file_ids=["a"], reset_all=True, run_clash_detection=False, build_scenes=False,
        ),
    )

    assert calls["load"]["reset"] is True
    assert calls["load"]["project_id"] is None
    assert calls["marked"] is True
    assert calls["reconciled"] == {project_id: ["a"]}
    assert records["a"]["status"] == "ingested"
    assert records["b"]["status"] == "uploaded"
    assert records["b"]["processing_status"] == "not_in_graph"
    assert response["load_summary"]["registry_files_removed_from_graph"] == 2


def test_load_reset_query_precedes_new_graph_writes(tmp_path, monkeypatch):
    nodes = tmp_path / "nodes.csv"
    edges = tmp_path / "edges.csv"
    nodes.write_text(
        "id,ifc_guid,type,name,storey_id,storey_name,min_x,min_y,min_z,max_x,max_y,max_z,"
        "source_ifc_file,source_file_id,discipline,project_id,coordinate_system_id\n"
        "p::f::g,g,IfcBeam,B,,,,,,,,,f.ifc,f,structure,p,c\n",
        encoding="utf-8",
    )
    edges.write_text("source_id,target_id,rel_type\n", encoding="utf-8")

    class Client:
        calls: list[str] = []
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def verify_connectivity(self): return None
        def run(self, query, params=None):
            type(self).calls.append(query)
            if "RETURN count(r) AS n" in query: return [{"n": 0}]
            return []
        def run_batched(self, query, rows, batch_size): type(self).calls.append(query)

    Client.calls = []
    monkeypatch.setattr(load_to_neo4j, "Neo4jClient", Client)
    load_to_neo4j.load(nodes, edges, reset=True)

    reset_index = next(i for i, query in enumerate(Client.calls) if "DETACH DELETE n" in query)
    write_index = next(i for i, query in enumerate(Client.calls) if query == load_to_neo4j.NODE_QUERY)
    assert reset_index < write_index
