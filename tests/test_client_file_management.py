"""Client-facing provenance and scoped file-management regressions."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api import routes
from bim_graph import project_registry, scene_export
from bim_graph.project_cleanup import delete_ifc_file_scope
from main import app
from rag import embedder
from rag.config import RAGSettings


class _NoopNeo4j:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def verify_connectivity(self):
        return None


def test_rag_delete_api_removes_one_then_multiple_and_preserves_unrelated(monkeypatch):
    chunks = {"pdf-a": 2, "pdf-b": 3, "pdf-c": 1}

    class Collection:
        def count(self):
            return sum(chunks.values())

    def delete_documents(document_ids):
        return {document_id: chunks.pop(document_id, 0) for document_id in document_ids}

    def documents():
        return [
            {"document_id": key, "filename": f"{key}.pdf", "chunk_count": count}
            for key, count in sorted(chunks.items())
        ]

    monkeypatch.setattr(routes, "_ensure_rag", lambda: None)
    monkeypatch.setattr(routes, "_RAG_EMBEDDER", {
        "delete_documents": delete_documents,
        "get_client_db": lambda: object(),
        "get_or_create_collection": lambda _client: Collection(),
        "list_indexed_documents": documents,
    })
    client = TestClient(app)

    single = client.post("/api/rag/documents/delete", json={"document_ids": ["pdf-a"]})
    assert single.status_code == 200
    assert single.json()["deleted_chunks"] == 2
    assert set(chunks) == {"pdf-b", "pdf-c"}

    multiple = client.post(
        "/api/rag/documents/delete", json={"document_ids": ["pdf-a", "pdf-c"]},
    )
    assert multiple.status_code == 200
    assert multiple.json()["deleted_documents"] == 1
    assert multiple.json()["deleted_by_document"] == {"pdf-a": 0, "pdf-c": 1}
    assert multiple.json()["remaining_chunks"] == 3
    assert [item["document_id"] for item in multiple.json()["documents"]] == ["pdf-b"]


def test_rag_delete_api_rejects_empty_selection():
    response = TestClient(app).post("/api/rag/documents/delete", json={"document_ids": []})
    assert response.status_code == 400


def test_chroma_bulk_delete_removes_only_selected_document_chunks(tmp_path):
    settings = RAGSettings(
        chroma_dir=str(tmp_path / "chroma"),
        collection_name="client-delete-test",
        embedding_provider="local",
        embedding_model="test-model",
    )
    collection = embedder.get_or_create_collection(settings=settings)
    collection.add(
        ids=["a-1", "a-2", "b-1", "c-1"],
        documents=["a one", "a two", "b one", "c one"],
        metadatas=[
            {"document_id": "a", "source": "a.pdf", "page_number": 1},
            {"document_id": "a", "source": "a.pdf", "page_number": 2},
            {"document_id": "b", "source": "b.pdf", "page_number": 1},
            {"document_id": "c", "source": "c.pdf", "page_number": 1},
        ],
        embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    )

    assert embedder.delete_documents(["a", "c"], settings) == {"a": 2, "c": 1}
    assert collection.get(where={"document_id": "a"}, include=[])["ids"] == []
    assert collection.get(where={"document_id": "c"}, include=[])["ids"] == []
    assert collection.get(where={"document_id": "b"}, include=[])["ids"] == ["b-1"]
    assert [item["document_id"] for item in embedder.list_indexed_documents(settings)] == ["b"]
    retrieved = collection.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)
    assert retrieved["metadatas"][0][0]["document_id"] == "b"


class _CleanupClient:
    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    def run(self, query, params=None):
        self.calls.append((query, params))
        if "RETURN count(r) AS n" in query:
            return [{"n": 4}]
        if "MATCH (e:Element)" in query and "RETURN count(e) AS n" in query:
            return [{"n": 7}]
        if "MATCH (m:IFCModel)" in query and "RETURN count(m) AS n" in query:
            return [{"n": 2}]
        if "RETURN count(run) AS n" in query:
            return [{"n": 1}]
        return []


def test_ifc_graph_cleanup_is_project_file_scoped_and_removes_touching_issues():
    client = _CleanupClient()
    summary = delete_ifc_file_scope(client, "project-a", ["structure", "architecture"])

    assert summary == {
        "elements_deleted": 7,
        "models_deleted": 2,
        "issues_deleted": 4,
        "sustainability_runs_deleted": 1,
    }
    queries = "\n".join(query for query, _params in client.calls)
    assert "OR (b.projectId = $project_id AND b.sourceFileId IN $file_ids)" in queries
    assert "e.projectId = $project_id" in queries
    assert "m.projectId = $project_id" in queries
    assert "run.projectId = $project_id" in queries
    assert "MATCH (m:Material)" not in queries
    assert all(
        params == {"project_id": "project-a", "file_ids": ["architecture", "structure"]}
        for _query, params in client.calls
    )


def _configure_ifc_storage(tmp_path, monkeypatch):
    project_root = tmp_path / "projects"
    monkeypatch.setattr(project_registry, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(project_registry, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(routes, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(scene_export, "SCENES_ROOT", tmp_path / "scenes")
    monkeypatch.setattr(routes, "Neo4jClient", _NoopNeo4j)
    cleanup_calls = []

    def cleanup(_client, project_id, file_ids):
        cleanup_calls.append((project_id, list(file_ids)))
        return {"elements_deleted": len(file_ids), "models_deleted": len(file_ids), "issues_deleted": 2}

    monkeypatch.setattr(routes, "delete_ifc_file_scope", cleanup)
    return project_root, cleanup_calls


def _register_ifc(project_root: Path, project_id: str, name: str, content: bytes):
    path = project_root / project_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    record = project_registry.register_uploaded_file(project_id, path, name)
    project_registry.update_file(record["project_id"], record["file_id"], status="ingested")
    scene_export.merge_manifest(project_id, {
        "file_id": record["file_id"], "source_ifc_file": name, "scenes": [],
    })
    directory = scene_export.scene_directory(project_id, record["file_id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.glb").write_bytes(b"glTF")
    return record, path, directory


def test_ifc_delete_api_removes_one_and_preserves_sibling_registry_file_and_scene(tmp_path, monkeypatch):
    project_root, cleanup_calls = _configure_ifc_storage(tmp_path, monkeypatch)
    first, first_path, first_scene = _register_ifc(project_root, "building", "a.ifc", b"A")
    second, second_path, second_scene = _register_ifc(project_root, "building", "b.ifc", b"B")

    response = TestClient(app).post(
        "/api/ifc/projects/building/files/delete", json={"file_ids": [first["file_id"]]},
    )

    assert response.status_code == 200
    assert response.json()["deleted_file_ids"] == [first["file_id"]]
    assert cleanup_calls == [("building", [first["file_id"]])]
    assert not first_path.exists() and not first_scene.exists()
    assert second_path.exists() and second_scene.exists()
    assert [item["file_id"] for item in project_registry.get_files("building")] == [second["file_id"]]
    assert set(scene_export.read_manifest("building")["files"]) == {second["file_id"]}


def test_ifc_delete_api_bulk_delete_preserves_unselected_file(tmp_path, monkeypatch):
    project_root, cleanup_calls = _configure_ifc_storage(tmp_path, monkeypatch)
    first, first_path, _ = _register_ifc(project_root, "building", "a.ifc", b"A")
    second, second_path, _ = _register_ifc(project_root, "building", "b.ifc", b"B")
    third, third_path, _ = _register_ifc(project_root, "building", "c.ifc", b"C")

    deleted_ids = [first["file_id"], third["file_id"]]
    response = TestClient(app).post(
        "/api/ifc/projects/building/files/delete", json={"file_ids": deleted_ids},
    )

    assert response.status_code == 200
    assert set(response.json()["deleted_file_ids"]) == set(deleted_ids)
    assert cleanup_calls == [("building", sorted(deleted_ids))]
    assert not first_path.exists() and not third_path.exists()
    assert second_path.exists()
    assert [item["file_id"] for item in project_registry.get_files("building")] == [second["file_id"]]


def test_ifc_delete_api_refuses_unmanaged_registry_path(tmp_path, monkeypatch):
    project_root, cleanup_calls = _configure_ifc_storage(tmp_path, monkeypatch)
    outside = tmp_path / "original.ifc"
    outside.write_bytes(b"never delete originals")
    record = project_registry.register_uploaded_file("building", outside, "outside.ifc")

    response = TestClient(app).post(
        "/api/ifc/projects/building/files/delete", json={"file_ids": [record["file_id"]]},
    )

    assert response.status_code == 409
    assert outside.exists()
    assert cleanup_calls == []
    assert project_registry.get_files("building")


def test_results_provenance_aliases_and_explicit_relation_scope(monkeypatch):
    class Client:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def run(self, _query, _params=None):
            return [{
                "a_id": "p::a::guid-a", "a_guid": "guid-a", "a_name": "Beam",
                "a_type": "IfcBeam", "a_source_ifc_file": "structure.ifc",
                "a_source_file_id": "a", "a_discipline": "structure",
                "b_id": "p::a::guid-b", "b_guid": "guid-b", "b_name": "Wall",
                "b_type": "IfcWall", "b_source_ifc_file": "structure.ifc",
                "b_source_file_id": "a", "b_discipline": "structure",
                "issue": "CLEARANCE_VIOLATION", "metric": 0.1,
                "project_id": "p", "cross_file": False,
            }]

    monkeypatch.setattr(routes, "Neo4jClient", Client)
    row = routes._run_issue_query(None, None, None, "p", ["a"])[0]

    assert row["element_a_id"] == "p::a::guid-a"
    assert row["element_a_guid"] == "guid-a"
    assert row["element_a_source_file"] == "structure.ifc"
    assert row["element_a_discipline"] == "structure"
    assert row["element_b_id"] == "p::a::guid-b"
    assert row["element_b_guid"] == "guid-b"
    assert row["relation_scope"] == "INTRA-FILE"


def test_frontend_file_management_provenance_scroll_menu_and_localization_contract():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")
    translations = Path("static/i18n.js").read_text(encoding="utf-8")

    positions = [html.index(f'data-tab="{tab}"') for tab in (
        "corpus", "pipeline", "chat", "results", "sustainability",
    )]
    assert positions == sorted(positions)
    assert 'class="table-container results-table-scroll"' in html
    assert 'class="data-table results-table"' in html
    assert "overflow-x: auto" in css and ".results-table {" in css and "min-width: 1760px" in css
    assert 'html[dir="ltr"] .results-table-scroll' in css
    assert 'html[dir="rtl"] .results-table-scroll' in css

    for field in (
        "element_a_id", "element_a_guid", "element_a_source_file", "element_a_discipline",
        "element_b_id", "element_b_guid", "element_b_source_file", "element_b_discipline",
    ):
        assert field in javascript
    assert 't("results.crossFile")' in javascript
    assert 't("results.intraFile")' in javascript
    assert 'id="corpus-select-all"' in html and 'id="delete-corpus-selected-btn"' in html
    assert 'id="ifc-delete-select-all"' in html and 'id="ifc-delete-selected-btn"' in html
    assert "selectedIfcDeleteIds" in javascript and "selectedIfcFileIds" in javascript
    assert 'data-delete-file-id=' in javascript and 'data-file-id=' in javascript
    assert 'fetch("/api/rag/documents/delete"' in javascript
    assert "/files/delete`" in javascript
    assert "window.bimViewer?.clear()" in javascript
    viewer = Path("static/viewer.js").read_text(encoding="utf-8")
    assert "cancelAndClear()" in viewer and "this.loadToken += 1" in viewer
    assert '"nav.corpus": "PDF Documents"' in translations
    assert '"nav.pipeline": "IFC Pipeline"' in translations
    assert '"nav.results": "Clash Results"' in translations
    assert '"nav.sustainability": "Sustainability Score"' in translations
    assert '"nav.corpus": "اسناد PDF"' in translations
    assert '"results.intraFile": "INTRA-FILE"' in translations
    assert '"results.intraFile": "درون‌فایلی"' in translations
    assert '/static/style.css?v=16' in html
    assert '/static/i18n.js?v=9' in html
    assert '/static/viewer.js?v=2' in html
    assert '/static/app.js?v=17' in html


def test_viewer_rename_documents_toolbar_and_results_export_scroll_contract():
    """UI contract for the client refinements: 3D Visualization naming, the
    simplified Documents toolbar, and the Results CSV export + top scrollbar."""
    html = Path("templates/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")
    translations = Path("static/i18n.js").read_text(encoding="utf-8")
    routes_src = Path("api/routes.py").read_text(encoding="utf-8")

    # 3D Visualization entry point: renamed everywhere user-visible, cube icon,
    # labelled trigger with aria-label, and no "3D map" left in the markup.
    assert 'class="rail-toggle viewer-toggle"' in html
    assert 'title="3D Visualization"' in html
    assert 'aria-label="3D Visualization"' in html
    assert 'data-i18n-title="viewer.title"' in html
    assert 'data-i18n-aria-label="viewer.title"' in html
    assert '<h3 data-i18n="viewer.title">3D Visualization</h3>' in html
    # Feather "box" (cube) glyph — distinct from the Pipeline layers icon.
    assert 'd="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8' in html
    assert 'title="3D map"' not in html
    assert '3D map' not in html
    assert '"viewer.title": "3D Visualization"' in translations
    assert '"viewer.title": "نمایش سه‌بعدی"' in translations

    # Documents toolbar: Select All + Delete Selected remain; Refresh and
    # Clear All are gone from the UI, and their handlers left app.js. The
    # backend /rag/clear endpoint is retained for compatibility.
    assert 'id="corpus-select-all"' in html and 'id="delete-corpus-selected-btn"' in html
    assert 'id="refresh-corpus-btn"' not in html
    assert 'id="clear-corpus-btn"' not in html
    assert 'clearCorpusBtn' not in javascript and 'refreshCorpusBtn' not in javascript
    assert "/api/rag/clear" not in javascript
    assert '@router.delete("/rag/clear")' in routes_src
    assert 'panel-header-actions file-management-actions' in html
    # The list still reloads itself on the automatic paths.
    assert 'async function loadCorpusStatus()' in javascript
    assert 'if (activeNav === "corpus") loadCorpusStatus();' in javascript

    # Results: synchronized top scrollbar above the table.
    assert 'id="results-top-scroll"' in html
    assert 'id="results-top-scroll-spacer"' in html
    assert 'aria-hidden="true"' in html
    assert 'function updateResultsTopScroll()' in javascript
    assert 'mirrorResultsScroll' in javascript
    assert 'new ResizeObserver(updateResultsTopScroll)' in javascript
    assert '.results-top-scroll.has-overflow' in css
    assert 'scrollbar-gutter: stable' in css
    assert 'html[dir="rtl"] .results-top-scroll { direction: rtl; }' in css

    # Results: CSV export of the currently visible rows.
    assert 'id="export-results-btn"' in html
    assert 'data-i18n="results.exportCsv"' in html
    assert 'function exportResultsCsv()' in javascript
    assert 'exportResultsBtn.disabled = total === 0' in javascript
    assert '"\\uFEFF"' in javascript  # UTF-8 BOM for Excel + Persian names
    assert '"results.exportCsv": "Export CSV"' in translations
    assert '"results.exportCsv": "خروجی CSV"' in translations
    assert '"results.csv.type"' in translations and '"results.csv.name"' in translations

    # Results: readability refinements (count line, issue badges, sticky column).
    assert 'id="results-count"' in html and 'aria-live="polite"' in html
    assert 'function issueBadgeHtml(' in javascript
    assert '"results.count"' in translations
    assert '"results.issueClash"' in translations and '"results.issueClearance"' in translations
    assert '.issue-badge' in css and '.issue-badge.issue-clash' in css
    assert '.results-table tbody tr:hover td' in css
    assert '.results-table thead th:first-child,\n.results-table tbody td:first-child' in css
    assert 'max-height: min(70vh, 820px)' in css

    # Clash review cards are the primary view; the wide table remains optional.
    assert 'id="results-card-view"' in html and 'id="results-cards"' in html
    assert 'id="results-table-wrap"' in html
    assert 'getElementById("results-table-wrap")' in javascript
    assert 'id="view-cards-btn"' in html and 'id="view-table-btn"' in html
    assert 'function setResultsView(' in javascript and 'setResultsView("cards")' in javascript
    assert 'function clashCardHtml(' in javascript
    assert 'function clashDetailsHtml(' in javascript
    assert 'data-action="details"' in javascript and 'data-action="copy"' in javascript
    assert 'data-action="view3d"' in javascript
    assert 'function showClashInViewer(' in javascript
    assert '"/api/model/elements/by-guid"' in javascript
    assert '"/model/elements/by-guid"' in routes_src
    assert 'RESULTS_PAGE_SIZE = 50' in javascript
    assert 'id="load-more-results-btn"' in html
    assert 'id="results-status"' in html and 'results-skeleton' in css
    assert '"results.loadError"' in translations and '"results.noResults"' in translations
    # Instant client filters over the loaded rows.
    assert 'id="results-search"' in html and 'id="source-model-filter"' in html
    assert 'id="results-sort"' in html and 'id="cross-file-filter"' in html
    assert 'function computeVisibleResults(' in javascript
    assert '"results.showingRange"' in translations
    # The results Refresh button is gone; loading happens on tab open, tab
    # switch, Apply, and pipeline completion.
    assert 'id="refresh-results-btn"' not in html
    assert 'refreshResultsBtn' not in javascript
    # Export CSV carries the full technical data including storey columns.
    assert 'r.aStorey' in javascript and 'r.bStorey' in javascript


def test_openapi_exposes_bulk_delete_contracts():
    schema = app.openapi()
    assert "/api/rag/documents/delete" in schema["paths"]
    assert "/api/ifc/projects/{project_id}/files/delete" in schema["paths"]
    assert "post" in schema["paths"]["/api/rag/documents/delete"]
    assert "post" in schema["paths"]["/api/ifc/projects/{project_id}/files/delete"]
