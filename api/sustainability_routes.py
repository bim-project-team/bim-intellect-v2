"""Project-scoped HTTP contracts for deterministic sustainability analysis."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from bim_graph.pipeline_lock import PIPELINE_LOCK
from sustainability.service import SustainabilityScopeError, SustainabilityService
from sustainability.reporting import (
    build_sustainability_report,
    report_to_csv,
    report_to_html,
    report_to_json,
)

router = APIRouter()
_SERVICE: SustainabilityService | None = None


def get_service() -> SustainabilityService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SustainabilityService()
    return _SERVICE


class SustainabilityAnalyzeRequest(BaseModel):
    project_id: str
    file_ids: Optional[list[str]] = None
    factor_dataset_version: Optional[str] = None
    region: Optional[str] = None
    allow_geometry_derived: bool = False


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except SustainabilityScopeError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(400, {"code": "invalid_sustainability_data", "message": str(exc)}) from exc


@router.post("/analyze")
def analyze_sustainability(request: SustainabilityAnalyzeRequest):
    with PIPELINE_LOCK:
        return _call(
            get_service().analyze,
            request.project_id,
            request.file_ids,
            factor_dataset_version=request.factor_dataset_version,
            region=request.region,
            allow_geometry_derived=request.allow_geometry_derived,
        )


@router.get("/summary")
def sustainability_summary(
    project_id: str,
    file_id: Optional[list[str]] = Query(None),
    run_id: Optional[str] = None,
):
    return _call(get_service().summary, project_id, file_id, run_id)


@router.get("/materials")
def sustainability_materials(
    project_id: str,
    file_id: Optional[list[str]] = Query(None),
    run_id: Optional[str] = None,
):
    return _call(get_service().materials, project_id, file_id, run_id=run_id)


@router.get("/elements")
def sustainability_elements(
    project_id: str,
    file_id: Optional[list[str]] = Query(None),
    run_id: Optional[str] = None,
    status: Optional[str] = None,
    ifc_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return _call(
        get_service().elements, project_id, file_id, run_id=run_id,
        status=status, ifc_type=ifc_type, limit=limit, offset=offset,
    )


@router.get("/factors")
def sustainability_factors(
    dataset_version: Optional[str] = None,
    region: Optional[str] = None,
):
    return _call(get_service().factors, dataset_version=dataset_version, region=region)


def _report_payload(
    project_id: str,
    file_ids: list[str] | None,
    run_id: str | None,
    conversation_id: str | None,
) -> dict:
    service = get_service()
    summary = _call(service.summary, project_id, file_ids, run_id)
    resolved_files = list(summary.get("file_ids") or [])
    elements = _call(
        service.elements, project_id, resolved_files,
        run_id=run_id, limit=1_000_000, offset=0,
    ).get("items", [])
    materials = _call(
        service.materials, project_id, resolved_files, run_id=run_id,
    ).get("items", [])
    from rag.memory import conversation_store
    findings = conversation_store.get_sustainability_findings(
        conversation_id,
        project_id=str(summary.get("project_id") or project_id),
        file_ids=resolved_files,
    )
    return build_sustainability_report(summary, elements, materials, findings)


@router.get("/assessment-findings")
def sustainability_assessment_findings(
    project_id: str,
    file_id: Optional[list[str]] = Query(None),
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
):
    summary = _call(get_service().summary, project_id, file_id, run_id)
    resolved_files = list(summary.get("file_ids") or [])
    from rag.memory import conversation_store
    items = conversation_store.get_sustainability_findings(
        conversation_id,
        project_id=str(summary.get("project_id") or project_id),
        file_ids=resolved_files,
    )
    return {
        "project_id": summary.get("project_id"),
        "file_ids": resolved_files,
        "run_id": (summary.get("run") or {}).get("id"),
        "total": len(items),
        "items": items,
        "persistence": "conversation_session",
    }


@router.get("/report")
def sustainability_report(
    project_id: str,
    file_id: Optional[list[str]] = Query(None),
    run_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    format: Literal["json", "csv", "html"] = "json",
):
    report = _report_payload(project_id, file_id, run_id, conversation_id)
    safe_project = str(report["project"].get("project_id") or "project")
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{safe_project}-sustainability-report.{format}"'
        )
    }
    if format == "csv":
        return Response(
            report_to_csv(report), media_type="text/csv; charset=utf-8", headers=headers,
        )
    if format == "html":
        return Response(
            report_to_html(report), media_type="text/html; charset=utf-8", headers=headers,
        )
    return Response(
        report_to_json(report), media_type="application/json; charset=utf-8", headers=headers,
    )
