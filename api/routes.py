"""
FastAPI routes for BIM-Intellect MVP.

Replaces the earlier ifc/graph/clash module split with the bim_graph
pipeline: extract_graph.py (IFC -> CSV, scoped parallel geometry) +
bim_graph.load_to_neo4j (CSV -> Neo4j, idempotent) +
bim_graph.clash_pipeline (Neo4j -> AABB clash/clearance detection,
type-pair-filtered, project/file-scoped, writes results back into Neo4j).

RAG endpoints now support hybrid retrieval:
- /ask          → Unified orchestrator (auto-routes to vector/graph/both)
- /ask-vector   → Debug: regulation-only (legacy retriever)
- /ask-graph    → Debug: graph-only

RAG corpus management:
- /rag/upload   → Upload one or more PDFs, chunk, embed, and store each independently
- /rag/ingest   → Ingest an existing PDF from disk into ChromaDB
- /rag/status   → Chroma collection stats
- /rag/clear    → Delete the Chroma collection
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator
from starlette.datastructures import UploadFile as StarletteUploadFile

from bim_graph.neo4j_client import Neo4jClient

from bim_graph.config import NODES_CSV, EDGES_CSV, DEFAULT_IFC_PATH
from bim_graph import load_to_neo4j, clash_pipeline
from extract_graph import run_extraction
from extract_graph import run_multi_extraction
from bim_graph.coordinate_system import inspect_coordinate_system, validate_federation
from bim_graph.project_registry import (
    PROJECT_ROOT, get_files, list_projects, register_uploaded_file, safe_id,
    update_file, utc_now, list_unregistered_ifc_files,
)
from bim_graph.pipeline_lock import PIPELINE_LOCK
from api.sustainability_routes import router as sustainability_router

router = APIRouter()
router.include_router(sustainability_router, prefix="/sustainability", tags=["sustainability"])
_PIPELINE_LOCK = PIPELINE_LOCK


def _normalize_upload_files(value):
    """Accept both one multipart file and repeated fields as an upload list.

    Some FastAPI/Pydantic combinations pass a single ``UploadFile`` through
    unchanged for an optional list field.  Normalizing before Pydantic's list
    validation keeps the wire format compatible across those versions.
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


OptionalUploadFiles = Annotated[
    Optional[List[UploadFile]],
    BeforeValidator(_normalize_upload_files),
]

# CSVs produced by extract_sotreys_type.py, at project root (sibling of api/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOREYS_CSV = os.path.join(_PROJECT_ROOT, "storeys.csv")
TYPES_CSV = os.path.join(_PROJECT_ROOT, "ifc_types.csv")


def _read_filter_csvs():
    """Read storeys.csv/ifc_types.csv into the shapes the frontend expects."""
    import csv as _csv

    storeys, types, errors = [], [], []

    if os.path.exists(STOREYS_CSV):
        with open(STOREYS_CSV, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                name = (row.get("name") or "").strip()
                if name:
                    storeys.append(name)
    else:
        errors.append(f"{STOREYS_CSV} not found.")

    if os.path.exists(TYPES_CSV):
        with open(TYPES_CSV, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                t = (row.get("ifc_type") or "").strip()
                if t:
                    types.append({"type": t, "count": int(row.get("count") or 0)})
    else:
        errors.append(f"{TYPES_CSV} not found.")

    return storeys, types, errors


class QuestionRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    use_strong_models: bool = False
    selected_element_id: Optional[str] = None
    project_id: Optional[str] = None
    file_ids: Optional[List[str]] = None


class ProjectIngestRequest(BaseModel):
    file_ids: List[str]
    storeys: Optional[List[str]] = None
    types: Optional[List[str]] = None
    reset_all: bool = False
    run_clash_detection: bool = True


# ------------------------------------------------------------------
# RAG module discovery
# ------------------------------------------------------------------


_RAG_CHUNKER = None
_RAG_EMBEDDER = None
_RAG_RETRIEVER = None
_RAG_ORCHESTRATOR = None
_RAG_IMPORT_ERROR = None


def _ensure_rag():
    """Lazy-load RAG modules. Fails fast with a descriptive message."""
    global _RAG_CHUNKER, _RAG_EMBEDDER, _RAG_RETRIEVER, _RAG_ORCHESTRATOR, _RAG_IMPORT_ERROR

    if _RAG_IMPORT_ERROR is not None:
        raise HTTPException(501, f"RAG modules failed to load: {_RAG_IMPORT_ERROR}")

    if _RAG_CHUNKER is None:
        try:
            from rag.chunker import chunk_pdf as _chunk_pdf
            _RAG_CHUNKER = _chunk_pdf
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"chunker import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_EMBEDDER is None:
        try:
            from rag.embedder import embed_and_store as _embed_and_store
            from rag.embedder import get_client_db as _get_client_db
            from rag.embedder import get_or_create_collection as _get_or_create_collection
            from rag.embedder import delete_document as _delete_document
            from rag.embedder import list_indexed_documents as _list_indexed_documents
            from rag.embedder import CHROMA_DIR as _CHROMA_DIR
            from rag.embedder import COLLECTION_NAME as _COLLECTION_NAME
            _RAG_EMBEDDER = {
                "embed_and_store": _embed_and_store,
                "get_client_db": _get_client_db,
                "get_or_create_collection": _get_or_create_collection,
                "delete_document": _delete_document,
                "list_indexed_documents": _list_indexed_documents,
                "CHROMA_DIR": _CHROMA_DIR,
                "COLLECTION_NAME": _COLLECTION_NAME,
            }
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"embedder import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_RETRIEVER is None:
        try:
            from rag.retriever import answer_question as _answer_question
            _RAG_RETRIEVER = _answer_question
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"retriever import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_ORCHESTRATOR is None:
        try:
            from rag.orchestrator import RAGOrchestrator as _RAGOrchestrator
            _RAG_ORCHESTRATOR = _RAGOrchestrator
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"orchestrator import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)


# ------------------------------------------------------------------
# IFC / Graph pipeline routes
# ------------------------------------------------------------------

@router.post("/extract")
def extract_ifc(
    ifc_path: str = Query(DEFAULT_IFC_PATH, description="Path to the .ifc file"),
    storey: Optional[List[str]] = Query(None, description="Repeatable: storey name(s) to include"),
    type_: Optional[List[str]] = Query(None, alias="type", description="Repeatable: IFC type(s) to include"),
):
    if not os.path.exists(ifc_path):
        raise HTTPException(404, f"IFC file not found: {ifc_path}")

    node_rows, edge_rows = run_extraction(
        ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
        storey_filter=storey, type_filter=type_,
    )

    return {
        "status": "ok",
        "ifc_path": ifc_path,
        "storey_filter": storey,
        "type_filter": type_,
        "extracted_nodes": len(node_rows),
        "extracted_edges": len(edge_rows),
        "nodes_csv": NODES_CSV,
        "edges_csv": EDGES_CSV,
    }


@router.post("/load")
def load_into_neo4j(
    reset: bool = Query(False, description="Wipe the graph before loading"),
):
    if not os.path.exists(NODES_CSV) or not os.path.exists(EDGES_CSV):
        raise HTTPException(
            409, f"{NODES_CSV}/{EDGES_CSV} not found - call /extract first."
        )
    return load_to_neo4j.load(nodes_csv=NODES_CSV, edges_csv=EDGES_CSV, reset=reset)


@router.post("/ingest")
def ingest_ifc(
    ifc_path: str = Query(DEFAULT_IFC_PATH, description="Path to the .ifc file"),
    storey: Optional[List[str]] = Query(None, description="Repeatable: storey name(s) to include"),
    type_: Optional[List[str]] = Query(None, alias="type", description="Repeatable: IFC type(s) to include"),
    reset: bool = Query(False, description="Wipe the graph before loading"),
):
    if not os.path.exists(ifc_path):
        raise HTTPException(404, f"IFC file not found: {ifc_path}")

    node_rows, edge_rows = run_extraction(
        ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
        storey_filter=storey, type_filter=type_,
    )
    load_summary = load_to_neo4j.load(nodes_csv=NODES_CSV, edges_csv=EDGES_CSV, reset=reset)

    return {
        "status": "ok",
        "ifc_path": ifc_path,
        "storey_filter": storey,
        "type_filter": type_,
        "extracted_nodes": len(node_rows),
        "extracted_edges": len(edge_rows),
        "load_summary": load_summary,
    }


@router.post("/analyze")
def analyze(
    anomaly_model_enabled: bool = False,
    anomaly_checkpoint: Optional[str] = None,
    anomaly_combination: str = Query("max", pattern="^(max|mean)$"),
    anomaly_device: str = "auto",
    project_id: Optional[str] = None,
    file_id: Optional[List[str]] = Query(None),
):
    if project_id:
        normalized_project_id = safe_id(project_id)
        all_records = get_files(normalized_project_id)
        if file_id:
            records = [record for record in all_records if record["file_id"] in set(file_id)]
            missing = sorted(set(file_id) - {record["file_id"] for record in records})
            if missing:
                raise HTTPException(404, f"Unknown file IDs for project {normalized_project_id}: {missing}")
        else:
            # The project's current analysis context consists of files that
            # completed graph import, not merely staged uploads.
            records = [record for record in all_records if record.get("status") == "ingested"]
            file_id = [record["file_id"] for record in records]
        not_ingested = [record["filename"] for record in records if record.get("status") != "ingested"]
        if not_ingested:
            raise HTTPException(409, f"Models are not imported into the graph: {not_ingested}")
        alignment = validate_federation(records)
        if not alignment["compatible"]:
            raise HTTPException(409, {"message": alignment["reason"], "alignment": alignment})
    summary = clash_pipeline.run_clash_detection(
        anomaly_model_enabled=anomaly_model_enabled,
        anomaly_checkpoint=anomaly_checkpoint,
        anomaly_combination=anomaly_combination,
        anomaly_device=anomaly_device,
        project_id=safe_id(project_id) if project_id else None,
        file_ids=file_id,
    )
    return summary


@router.post("/ifc/upload")
def upload_ifc(
    files: OptionalUploadFiles = File(None, description="One or more IFC files"),
    file: Optional[UploadFile] = File(None, description="Legacy single-file field"),
    project_id: str = Form("default-project"),
    discipline: str = Form("unspecified"),
):
    """Upload/parse files independently; a bad IFC does not roll back valid siblings."""
    from extract_sotreys_type import scan_ifc

    uploads = list(files or []) + ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(400, "Select at least one IFC file.")
    project_id = safe_id(project_id)
    project_dir = PROJECT_ROOT / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    results = []
    all_storeys, type_counts = set(), {}
    for upload in uploads:
        filename = Path(upload.filename or "").name
        temporary_path = project_dir / f".{uuid.uuid4().hex}.upload"
        stored_path = None
        stored_preexisting = False
        try:
            if not filename.lower().endswith(".ifc"):
                raise ValueError("Only .ifc files are accepted.")
            with temporary_path.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            from bim_graph.project_registry import file_sha256
            digest = file_sha256(temporary_path)
            stored_path = project_dir / f"{digest[:12]}__{filename}"
            stored_preexisting = stored_path.exists()
            os.replace(temporary_path, stored_path)
            scan = scan_ifc(stored_path)
            coordinate_system = inspect_coordinate_system(stored_path)
            record = register_uploaded_file(
                project_id, stored_path, filename, discipline=discipline,
            )
            record = update_file(
                project_id, record["file_id"], schema=scan["schema"],
                coordinate_system=coordinate_system, processing_status="ready_for_ingestion",
                storeys=scan["storeys"], types=scan["types"],
            )
            all_storeys.update(scan["storeys"])
            for item in scan["types"]:
                type_counts[item["type"]] = type_counts.get(item["type"], 0) + item["count"]
            results.append({**record, "status": "uploaded", "storeys": scan["storeys"], "types": scan["types"]})
        except Exception as exc:
            if temporary_path.exists():
                temporary_path.unlink()
            if stored_path is not None and stored_path.exists() and not stored_preexisting:
                stored_path.unlink()
            results.append({"filename": filename or "unknown", "status": "failed", "error": str(exc)})
        finally:
            upload.file.close()
    succeeded = [item for item in results if item["status"] == "uploaded"]
    response = {
        "status": "ok" if len(succeeded) == len(results) else "partial_success",
        "project_id": project_id,
        "succeeded": len(succeeded), "failed": len(results) - len(succeeded),
        "files": results,
        "storeys": sorted(all_storeys),
        "types": [{"type": name, "count": count} for name, count in sorted(type_counts.items())],
    }
    # Preserve the old response shape for clients posting the legacy `file` field.
    if file is not None and not files and succeeded:
        return {**succeeded[0], **response, "status": "ok", "ifc_path": succeeded[0]["stored_path"], "errors": []}
    return response


@router.get("/ifc/projects")
def get_ifc_projects():
    return {"projects": list_projects(), "legacy_unregistered_files": list_unregistered_ifc_files()}


@router.post("/ifc/projects/{project_id}/ingest")
def ingest_ifc_project(project_id: str, req: ProjectIngestRequest):
    project_id = safe_id(project_id)
    records = get_files(project_id, req.file_ids)
    missing = sorted(set(req.file_ids) - {record["file_id"] for record in records})
    if missing:
        raise HTTPException(404, f"Unknown file IDs for project {project_id}: {missing}")
    if not records:
        raise HTTPException(400, "Select at least one uploaded IFC model.")
    alignment = validate_federation(records)
    if not alignment["compatible"]:
        raise HTTPException(409, {"message": alignment["reason"], "alignment": alignment})

    with _PIPELINE_LOCK:
        for record in records:
            update_file(project_id, record["file_id"], processing_status="extracting", error=None)
        try:
            nodes, edges, per_file = run_multi_extraction(
                records, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
                storey_filter=req.storeys, type_filter=req.types,
            )
            successful_ids = {item["file_id"] for item in per_file if item["status"] == "processed"}
            if not successful_ids:
                raise RuntimeError("No selected IFC file completed geometry extraction.")
            load_summary = load_to_neo4j.load(
                nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
                reset=req.reset_all, project_id=None if req.reset_all else project_id,
            )
            per_file_map = {item["file_id"]: item for item in per_file}
            selected_ids = successful_ids
            for existing in get_files(project_id):
                if existing["file_id"] not in selected_ids and existing.get("status") == "ingested":
                    update_file(project_id, existing["file_id"], status="uploaded", processing_status="not_in_graph")
            for record in records:
                counts = per_file_map[record["file_id"]]
                if counts["status"] == "processed":
                    update_file(
                        project_id, record["file_id"], status="ingested", processing_status="imported",
                        ingested_at=utc_now(), node_count=counts["nodes"], edge_count=counts["edges"], error=None,
                    )
                else:
                    update_file(
                        project_id, record["file_id"], status="uploaded", processing_status="failed",
                        error=counts.get("error"),
                    )
            analysis = None
            if req.run_clash_detection:
                analysis = clash_pipeline.run_clash_detection(project_id=project_id, file_ids=sorted(successful_ids))
                for record in records:
                    if record["file_id"] in successful_ids:
                        update_file(project_id, record["file_id"], processing_status="analyzed", analyzed_at=utc_now())
            return {
                "status": "ok" if len(successful_ids) == len(records) else "partial_success",
                "project_id": project_id, "alignment": alignment,
                "files": get_files(project_id, req.file_ids), "per_file": per_file,
                "extracted_nodes": len(nodes), "extracted_edges": len(edges),
                "load_summary": load_summary, "analysis": analysis,
            }
        except HTTPException:
            raise
        except Exception as exc:
            for record in records:
                update_file(project_id, record["file_id"], processing_status="failed", error=str(exc))
            raise HTTPException(500, f"Project ingestion failed: {exc}")


@router.get("/filters/dataset")
def get_dataset_filters(
    ifc_path: Optional[str] = Query(None, description="Rescan just this IFC file. Omit to rescan the whole dataset/ifc/ directory."),
    refresh: bool = Query(False, description="Force a rescan even if the CSVs already exist"),
):
    """
    Storeys and IFC types, read from storeys.csv / ifc_types.csv at project
    root. When ifc_path is given, a (re)scan is scoped to just that file —
    used after the user uploads/picks a specific IFC file in the pipeline
    form. Without ifc_path, falls back to scanning the whole dataset/ifc/
    directory (only used by the untargeted "Rescan" affordance, if any).
    """
    from extract_sotreys_type import DEFAULT_IFC_DIR, main as _extract_main

    target = ifc_path or DEFAULT_IFC_DIR
    need_generate = refresh or bool(ifc_path) or not (os.path.exists(STOREYS_CSV) and os.path.exists(TYPES_CSV))
    errors = []

    if need_generate:
        if ifc_path and not os.path.exists(ifc_path):
            errors.append(f"IFC file not found: {ifc_path}")
        elif not ifc_path and (not os.path.isdir(target) or not any(
            f.lower().endswith(".ifc") for f in os.listdir(target)
        )):
            errors.append(f"No .ifc files found in {target} - add IFC files first.")
        else:
            try:
                _extract_main(target, storeys_csv=STOREYS_CSV, types_csv=TYPES_CSV)
            except Exception as exc:
                errors.append(f"Failed to scan {target}: {exc}")

    storeys, types, read_errors = _read_filter_csvs()
    if not errors:
        errors = read_errors

    return {"storeys": storeys, "types": types, "generated": need_generate and not errors, "errors": errors}


@router.get("/filters/storeys")
def get_available_storeys():
    """
    Dynamically searches the Neo4j graph for available storeys, so the
    Results tab filter reflects whatever was actually ingested (post
    storey/type filtering), not the source IFC file's full storey list.
    """
    try:
        with Neo4jClient() as client:
            query = """
            MATCH (n:Element)
            WHERE n.storeyName IS NOT NULL AND n.storeyName <> ""
            RETURN DISTINCT n.storeyName AS storey
            ORDER BY storey
            """
            records = client.run(query)
            return {"storeys": [r["storey"] for r in records]}
    except Exception as e:
        print(f"[routes] /filters/storeys failed: {e}", file=sys.stderr)
        return {"storeys": [], "error": str(e)}


@router.get("/filters/types")
def get_available_types():
    """
    Dynamically searches the Neo4j graph for the IFC types actually
    present, for the Results tab's type filter (mirrors /filters/storeys).
    """
    try:
        with Neo4jClient() as client:
            query = """
            MATCH (n:Element)
            WHERE n.ifcType IS NOT NULL AND n.ifcType <> ""
            RETURN DISTINCT n.ifcType AS type
            ORDER BY type
            """
            records = client.run(query)
            return {"types": [r["type"] for r in records]}
    except Exception as e:
        print(f"[routes] /filters/types failed: {e}", file=sys.stderr)
        return {"types": [], "error": str(e)}


def _run_issue_query(issue_type, storey, types, project_id=None):
    """
    Shared Cypher for /clashes, /violations, /issues. Property names here
    must match what load_to_neo4j.py / clash_pipeline.py actually write:
    r.issue (not r.issue_type), a.ifcType/b.ifcType (not ifc_type),
    a.storeyName/b.storeyName (not storey_name).
    """
    with Neo4jClient() as client:
        where_clauses = []
        # Dynamic property lookup prevents Neo4j's unknown-property-token
        # warning when rule-only runs have never created anomaly fields.
        # Missing optional values still come back as null.
        params = {
            "anomaly_score_a_property": "anomalyScoreA",
            "anomaly_score_b_property": "anomalyScoreB",
            "combined_anomaly_score_property": "combinedAnomalyScore",
        }
        if issue_type:
            where_clauses.append("r.issue = $issue_type")
            params["issue_type"] = issue_type
        if storey:
            where_clauses.append("(a.storeyName = $storey OR b.storeyName = $storey)")
            params["storey"] = storey
        if types:
            type_list = [t.strip() for t in types.split(",") if t.strip()]
            if type_list:
                where_clauses.append("(a.ifcType IN $types OR b.ifcType IN $types)")
                params["types"] = type_list
        if project_id:
            where_clauses.append("r.projectId = $project_id")
            params["project_id"] = safe_id(project_id)
        where_string = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
        MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
        {where_string}
        RETURN a.id AS a_id, coalesce(a.ifcGuid, a.id) AS a_guid,
               a.ifcType AS a_type, a.name AS a_name,
               a.sourceIfcFile AS a_source_ifc_file, a.discipline AS a_discipline,
               b.id AS b_id, coalesce(b.ifcGuid, b.id) AS b_guid,
               b.ifcType AS b_type, b.name AS b_name,
               b.sourceIfcFile AS b_source_ifc_file, b.discipline AS b_discipline,
               r.issue AS issue, r.metric AS metric,
               r.projectId AS project_id, r.crossFile AS cross_file,
               r[$anomaly_score_a_property] AS anomaly_score_a,
               r[$anomaly_score_b_property] AS anomaly_score_b,
               r[$combined_anomaly_score_property] AS combined_anomaly_score
        ORDER BY r.metric DESC
        LIMIT 1000
        """
        records = client.run(query, params)
        return [dict(r) for r in records]


@router.get("/clashes")
def get_clashes_filtered(storey: Optional[str] = None, types: Optional[str] = None, project_id: Optional[str] = None):
    try:
        return _run_issue_query("CLASH", storey, types, project_id)
    except Exception as e:
        print(f"[routes] /clashes failed: {e}", file=sys.stderr)
        return []


@router.get("/violations")
def get_violations_filtered(storey: Optional[str] = None, types: Optional[str] = None, project_id: Optional[str] = None):
    try:
        return _run_issue_query("CLEARANCE_VIOLATION", storey, types, project_id)
    except Exception as e:
        print(f"[routes] /violations failed: {e}", file=sys.stderr)
        return []


@router.get("/issues")
def get_issues_filtered(storey: Optional[str] = None, types: Optional[str] = None, project_id: Optional[str] = None):
    try:
        return _run_issue_query(None, storey, types, project_id)
    except Exception as e:
        print(f"[routes] /issues failed: {e}", file=sys.stderr)
        return []


# ------------------------------------------------------------------
# RAG corpus management: upload / ingest / status / clear
# ------------------------------------------------------------------

@router.post(
    "/rag/upload",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "One or more PDF files",
                            },
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "Legacy single-file field",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def upload_pdf(
    request: Request,
    doc_id: Optional[str] = Query(None, description="Document ID prefix for chunk IDs (defaults to filename stem)"),
    document_domain: Optional[str] = Query(None, description="regulation, sustainability, leed, or standard"),
    standard_name: Optional[str] = Query(None),
    standard_version: Optional[str] = Query(None),
):
    # Read repeated multipart values directly.  Older FastAPI/Pydantic
    # combinations can incorrectly validate one UploadFile as a list or reject
    # repeated fields before the endpoint is called.
    form = await request.form()
    uploads = list(form.getlist("files")) + list(form.getlist("file"))
    invalid_parts = [item for item in uploads if not isinstance(item, StarletteUploadFile)]
    if invalid_parts:
        raise HTTPException(422, "The 'files' and 'file' fields must contain uploaded files.")

    _ensure_rag()
    from rag.document_metadata import classify_document
    try:
        classification = classify_document(document_domain, standard_name, standard_version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not uploads:
        raise HTTPException(400, "Select at least one PDF file.")
    results = []
    for upload in uploads:
        filename = Path(upload.filename or "").name
        tmp_path = None
        try:
            if not filename.lower().endswith(".pdf"):
                raise ValueError("Only PDF files are accepted.")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                shutil.copyfileobj(upload.file, tmp)
                tmp_path = tmp.name
            from bim_graph.project_registry import file_sha256
            automatic_id = f"{Path(filename).stem}-{file_sha256(tmp_path)[:12]}"
            effective_doc_id = doc_id if len(uploads) == 1 and doc_id else automatic_id
            chunks = _RAG_CHUNKER(
                tmp_path, doc_id=effective_doc_id, source=filename,
                document_domain=classification.document_domain,
                standard_name=classification.standard_name,
                standard_version=classification.standard_version,
            )
            if not chunks:
                raise ValueError("No text could be extracted from the PDF.")
            replaced_count = _RAG_EMBEDDER["delete_document"](effective_doc_id)
            stored_count = _RAG_EMBEDDER["embed_and_store"](chunks)
            results.append({
                "status": "indexed", "filename": filename, "doc_id": effective_doc_id,
                "chunks_extracted": len(chunks), "chunks_stored": stored_count,
                "stale_chunks_replaced": replaced_count,
                "document_domain": classification.document_domain,
                "standard_name": classification.standard_name,
                "standard_version": classification.standard_version,
            })
        except Exception as exc:
            results.append({"status": "failed", "filename": filename or "unknown", "error": str(exc)})
        finally:
            upload.file.close()
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    succeeded = [item for item in results if item["status"] == "indexed"]
    response = {
        "status": "ok" if len(succeeded) == len(results) else "partial_success",
        "succeeded": len(succeeded), "failed": len(results) - len(succeeded),
        "files": results, "chroma_dir": _RAG_EMBEDDER["CHROMA_DIR"],
        "collection": _RAG_EMBEDDER["COLLECTION_NAME"],
    }
    if len(form.getlist("file")) == 1 and not form.getlist("files") and succeeded:
        return {**succeeded[0], **response, "status": "ok"}
    return response


@router.post("/rag/ingest")
def ingest_pdf_from_path(
    pdf_path: str = Query(..., description="Absolute or relative path to an existing PDF file"),
    doc_id: Optional[str] = Query(None, description="Document ID prefix for chunk IDs (defaults to filename stem)"),
    document_domain: Optional[str] = Query(None, description="regulation, sustainability, leed, or standard"),
    standard_name: Optional[str] = Query(None),
    standard_version: Optional[str] = Query(None),
):
    _ensure_rag()

    from rag.document_metadata import classify_document
    try:
        classification = classify_document(document_domain, standard_name, standard_version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not os.path.exists(pdf_path):
        raise HTTPException(404, f"PDF file not found: {pdf_path}")

    if not pdf_path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    if not doc_id:
        doc_id = Path(pdf_path).stem

    try:
        chunks = _RAG_CHUNKER(
            pdf_path, doc_id=doc_id, source=Path(pdf_path).name,
            document_domain=classification.document_domain,
            standard_name=classification.standard_name,
            standard_version=classification.standard_version,
        )
        if not chunks:
            raise HTTPException(400, "No text could be extracted from the PDF.")

        replaced_count = _RAG_EMBEDDER["delete_document"](doc_id)
        stored_count = _RAG_EMBEDDER["embed_and_store"](chunks)

        return {
            "status": "ok",
            "pdf_path": pdf_path,
            "doc_id": doc_id,
            "chunks_extracted": len(chunks),
            "chunks_stored": stored_count,
            "stale_chunks_replaced": replaced_count,
            "document_domain": classification.document_domain,
            "standard_name": classification.standard_name,
            "standard_version": classification.standard_version,
            "chroma_dir": _RAG_EMBEDDER["CHROMA_DIR"],
            "collection": _RAG_EMBEDDER["COLLECTION_NAME"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"PDF ingestion failed: {exc}")


@router.get("/rag/status")
def rag_status():
    _ensure_rag()

    try:
        client = _RAG_EMBEDDER["get_client_db"]()
        collection = _RAG_EMBEDDER["get_or_create_collection"](client)
        count = collection.count()
        sample = collection.peek(limit=3) if count > 0 else {}

        return {
            "status": "ok",
            "chroma_dir": _RAG_EMBEDDER["CHROMA_DIR"],
            "collection": _RAG_EMBEDDER["COLLECTION_NAME"],
            "document_count": count,
            "sample_ids": sample.get("ids", [])[:3] if sample else [],
            "index_metadata": collection.metadata or {},
            "documents": _RAG_EMBEDDER["list_indexed_documents"](),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to query Chroma status: {exc}")


@router.delete("/rag/clear")
def rag_clear_collection():
    _ensure_rag()

    try:
        client = _RAG_EMBEDDER["get_client_db"]()
        client.delete_collection(name=_RAG_EMBEDDER["COLLECTION_NAME"])
        return {
            "status": "ok",
            "message": f"Collection \"{_RAG_EMBEDDER['COLLECTION_NAME']}\" deleted.",
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to clear collection: {exc}")


# ------------------------------------------------------------------
# RAG Q&A routes (hybrid vector + graph)
# ------------------------------------------------------------------

@router.post("/ask")
def ask(req: QuestionRequest):
    _ensure_rag()

    orchestrator = _RAG_ORCHESTRATOR()
    try:
        result = orchestrator.ask(
            req.question,
            conversation_id=req.conversation_id,
            use_strong_models=req.use_strong_models,
            project_id=req.project_id,
            file_ids=req.file_ids,
        )
    except Exception as exc:
        raise HTTPException(500, f"RAG pipeline error: {exc}")

    return result


@router.delete("/rag/conversations/{conversation_id}")
def clear_rag_conversation(conversation_id: str):
    _ensure_rag()
    from rag.memory import conversation_store
    return {
        "status": "ok",
        "conversation_id": conversation_id,
        "cleared": conversation_store.clear(conversation_id),
    }


@router.post("/ask-vector")
def ask_vector_only(req: QuestionRequest):
    _ensure_rag()

    try:
        return _RAG_RETRIEVER(req.question)
    except Exception as exc:
        raise HTTPException(500, f"Vector retrieval error: {exc}")


@router.post("/ask-graph")
def ask_graph_only(req: QuestionRequest):
    try:
        from bim_graph.graph_retriever import GraphRetriever
    except ImportError as exc:
        raise HTTPException(501, f"Graph retriever not available: {exc}")

    gr = GraphRetriever()
    try:
        return gr.ask(req.question)
    except Exception as exc:
        raise HTTPException(500, f"Graph query error: {exc}")


# ------------------------------------------------------------------
# System Health Check
# ------------------------------------------------------------------

@router.get("/health/")
@router.get("/health")
def health_check():
    """
    Check the health of the API and its dependencies (Neo4j, ChromaDB).
    Returns 200 if all is well, 503 if a dependency is unreachable.
    """
    healthy = True
    checks = {}

    # 1. Check Neo4j
    try:
        with Neo4jClient() as client:
            client.run("RETURN 1")
        checks["neo4j"] = {"status": "ok"}
    except Exception as e:
        checks["neo4j"] = {"status": "error", "detail": "Neo4j connection failed"}
        healthy = False

    # 2. Check ChromaDB
    try:
        _ensure_rag()
        client = _RAG_EMBEDDER["get_client_db"]()
        collection = _RAG_EMBEDDER["get_or_create_collection"](client)
        count = collection.count()
        checks["chroma"] = {
            "status": "ok",
            "document_count": count,
            "collection": _RAG_EMBEDDER["COLLECTION_NAME"]
        }
    except Exception as e:
        checks["chroma"] = {"status": "error", "detail": "ChromaDB connection failed"}
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "service": "bim-intellect",
            "checks": checks
        }
    )
