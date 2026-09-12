from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from rag.config import RAGSettings
from rag.memory import ConversationStore, conversation_store
from sustainability.calculator import summarize_results
from sustainability.reporting import (
    REPORT_DISCLAIMER,
    build_sustainability_report,
    report_to_csv,
    report_to_html,
)


def result_row(**updates):
    row = {
        "id": "result-1",
        "run_id": "run-1",
        "element_id": "project-a::file-a::guid-1",
        "ifc_guid": "guid-1",
        "ifc_type": "IfcWall",
        "element_name": "External wall",
        "project_id": "project-a",
        "source_file_id": "file-a",
        "source_ifc_file": "architecture.ifc",
        "discipline": "architecture",
        "material_raw": "Test concrete",
        "material_normalized": "test concrete",
        "mapping_status": "matched",
        "raw_quantity": 2.5,
        "raw_quantity_unit": "m3",
        "normalized_quantity": 2.5,
        "normalized_quantity_unit": "m3",
        "quantity_source": "ifc_explicit",
        "factor_id": "fixture-concrete",
        "factor_value": 100.0,
        "factor_unit": "kgCO2e/m3",
        "factor_source": "Explicit test fixture",
        "factor_source_version": "1",
        "factor_dataset_version": "fixture-v1",
        "factor_dataset_hash": "fixture-hash",
        "factor_record_id": "fixture-hash:fixture-concrete",
        "carbon_kgco2e": 250.0,
        "calculation_status": "calculated_explicit",
        "calculation_note": "whole_element_single_material",
    }
    row.update(updates)
    return row


def report_summary(rows=None):
    rows = rows or [result_row()]
    run = {
        "id": "run-1",
        "project_id": "project-a",
        "file_ids": ["file-a"],
        "completedAt": "2026-09-05T12:00:00+00:00",
        "methodologyVersion": "embodied-carbon-v1",
        "extractorVersion": "ifc-sustainability-v1",
        "factorDatasetVersion": "fixture-v1",
        "factorDatasetHash": "fixture-hash",
        "factorRegion": "fixture-region",
        "allowGeometryDerived": False,
    }
    return summarize_results(rows, run)


def test_summary_exposes_element_level_explicit_and_estimated_coverage():
    rows = [
        result_row(),
        result_row(
            id="result-2", element_id="project-a::file-a::guid-2", ifc_guid="guid-2",
            quantity_source="geometry_derived", calculation_status="calculated_estimate",
            carbon_kgco2e=50.0,
        ),
        result_row(
            id="result-3", element_id="project-a::file-a::guid-3", ifc_guid="guid-3",
            material_raw=None, material_normalized=None, mapping_status="unmatched",
            normalized_quantity=None, normalized_quantity_unit=None, factor_id=None,
            factor_value=None, factor_unit=None, factor_source=None, carbon_kgco2e=None,
            calculation_status="missing_material",
        ),
    ]
    summary = report_summary(rows)
    assert summary["elements_with_calculated_carbon"] == 2
    assert summary["elements_not_evaluated"] == 1
    assert summary["elements_with_explicit_quantities"] == 1
    assert summary["elements_with_estimated_quantities"] == 1
    assert summary["explicit_quantity_coverage_ratio"] == 1 / 3
    assert summary["estimated_quantity_coverage_ratio"] == 1 / 3


def test_report_contains_reproducible_scope_provenance_quality_and_leed_findings():
    row = result_row()
    summary = report_summary([row])
    finding = {
        "criterion": "What does the retrieved material criterion require?",
        "assessment_status": "not_automatically_evaluable",
        "evidence": "Fixture-grounded explanation.",
        "project_id": "project-a",
        "file_ids": ["file-a"],
        "document_citations": [{
            "document_id": "leed-fixture",
            "section_id": "MR-fixture",
            "page_number": 12,
        }],
        "missing_project_data": ["product declarations"],
        "session_only": True,
    }
    report = build_sustainability_report(
        summary, [row], [{"material_normalized": "test concrete"}], [finding],
        generated_at="2026-09-05T13:00:00+00:00",
    )
    assert report["project"]["project_id"] == "project-a"
    assert report["project"]["selected_ifc_models"][0]["source_ifc_file"] == "architecture.ifc"
    assert report["summary"]["calculated_carbon_kgco2e"] == 250.0
    assert report["top_contributors"][0]["ifc_guid"] == "guid-1"
    assert report["carbon_factor_citations"][0]["source"] == "Explicit test fixture"
    assert report["leed_oriented_findings"][0]["assessment_status"] == "not_automatically_evaluable"
    assert REPORT_DISCLAIMER in report["limitations"]

    csv_text = report_to_csv(report)
    html_text = report_to_html(report)
    assert "250.0" in csv_text and "fixture-concrete" in csv_text
    assert "leed-fixture / MR-fixture / p.12" in csv_text
    assert "Sustainability and embodied-carbon report" in html_text
    assert "LEED Gold" not in html_text


def test_conversation_findings_are_bounded_and_exact_project_file_scoped():
    store = ConversationStore(RAGSettings())
    state = store.get("phase3-chat")
    store.record_sustainability_finding(state, {
        "criterion": "Criterion A", "project_id": "project-a",
        "file_ids": ["file-b", "file-a"], "assessment_status": "insufficient_evidence",
    })
    store.record_sustainability_finding(state, {
        "criterion": "Criterion B", "project_id": "project-b",
        "file_ids": ["file-a"], "assessment_status": "not_automatically_evaluable",
    })
    found = store.get_sustainability_findings(
        "phase3-chat", project_id="project-a", file_ids=["file-a", "file-b"],
    )
    assert [item["criterion"] for item in found] == ["Criterion A"]
    assert store.get_sustainability_findings(
        "phase3-chat", project_id="project-a", file_ids=["file-a"],
    ) == []


def test_report_and_assessment_api_preserve_exact_scope_and_download_contract(monkeypatch):
    import api.sustainability_routes as routes

    row = result_row()
    summary = report_summary([row])

    class Service:
        def summary(self, project_id, file_ids, run_id=None):
            assert project_id == "project-a"
            assert file_ids in (["file-a"], None)
            assert run_id in ("run-1", None)
            return summary

        def elements(self, project_id, file_ids, **kwargs):
            assert project_id == "project-a" and file_ids == ["file-a"]
            return {"total": 1, "items": [row]}

        def materials(self, project_id, file_ids, **kwargs):
            assert project_id == "project-a" and file_ids == ["file-a"]
            return {"total": 1, "items": [{"material_normalized": "test concrete"}]}

    monkeypatch.setattr(routes, "_SERVICE", Service())
    state = conversation_store.get("phase3-api-chat")
    conversation_store.record_sustainability_finding(state, {
        "criterion": "Fixture criterion",
        "assessment_status": "not_automatically_evaluable",
        "evidence": "Fixture evidence",
        "project_id": "project-a",
        "file_ids": ["file-a"],
        "document_citations": [],
        "missing_project_data": [],
    })
    client = TestClient(app)
    base = (
        "/api/sustainability/report?project_id=project-a&file_id=file-a"
        "&run_id=run-1&conversation_id=phase3-api-chat"
    )
    json_response = client.get(base + "&format=json")
    csv_response = client.get(base + "&format=csv")
    html_response = client.get(base + "&format=html")
    findings = client.get(
        "/api/sustainability/assessment-findings?project_id=project-a"
        "&file_id=file-a&run_id=run-1&conversation_id=phase3-api-chat"
    )
    assert json_response.status_code == csv_response.status_code == html_response.status_code == 200
    assert json_response.json()["summary"]["calculated_carbon_kgco2e"] == 250.0
    assert "attachment; filename=" in json_response.headers["content-disposition"]
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert html_response.headers["content-type"].startswith("text/html")
    assert findings.json()["items"][0]["criterion"] == "Fixture criterion"
    conversation_store.clear("phase3-api-chat")


def test_frontend_sustainability_contract_is_project_safe_bilingual_and_additive():
    root = Path(__file__).parents[1]
    html = (root / "templates" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
    sustainability_js = (root / "static" / "sustainability.js").read_text(encoding="utf-8")
    i18n = (root / "static" / "i18n.js").read_text(encoding="utf-8")

    assert 'data-tab="sustainability"' in html
    assert 'id="tab-sustainability"' in html
    assert 'value="selected"' in html and 'value="all"' in html
    assert 'id="sustainability-contributors-body"' in html
    assert 'id="sustainability-assessment-body"' in html
    assert 'data-format="json"' in html and 'data-format="csv"' in html
    assert 'data-format="html"' in html
    assert "What is the estimated embodied carbon of this project?" in html
    assert "کربن نهفته برآوردشده این پروژه چقدر است؟" in html
    assert 'dir="auto"' in html

    assert "payload.project_id = activeProjectId" in app_js
    assert "payload.file_ids = Array.from(selectedIfcFileIds)" in app_js
    assert 'uploadUrl.searchParams.set("document_domain", documentDomain)' in app_js
    assert "assertSustainabilityScope" in sustainability_js
    assert "sustainabilityState.requestToken" in sustainability_js
    assert 'searchParams.append("file_id", id)' in sustainability_js
    assert "/api/sustainability/assessment-findings" in sustainability_js
    assert "/api/sustainability/report" in sustainability_js
    assert i18n.count('"nav.sustainability"') == 2
    assert i18n.count('"sustainability.noFindings"') == 2


def test_openapi_includes_phase3_sustainability_endpoints():
    paths = app.openapi()["paths"]
    assert "/api/sustainability/report" in paths
    assert "/api/sustainability/assessment-findings" in paths
