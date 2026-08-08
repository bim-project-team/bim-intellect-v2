"""
FastAPI routes for BIM-Intellect MVP.

Replaces the earlier ifc/graph/clash module split with the bim_graph
pipeline: extract_graph.py (IFC -> CSV, scoped parallel geometry) +
bim_graph.load_to_neo4j (CSV -> Neo4j, idempotent) +
bim_graph.clash_pipeline (Neo4j -> AABB clash/clearance detection,
type-pair-filtered, storey-scoped, writes results back into Neo4j).

RAG endpoints now support hybrid retrieval:
- /ask          → Unified orchestrator (auto-routes to vector/graph/both)
- /ask-vector   → Debug: regulation-only (legacy retriever)
- /ask-graph    → Debug: graph-only

RAG corpus management:
- /rag/upload   → Upload a PDF, chunk it, embed it, store in ChromaDB
- /rag/ingest   → Ingest an existing PDF from disk into ChromaDB
- /rag/status   → Chroma collection stats
- /rag/clear    → Delete the Chroma collection
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bim_graph.neo4j_client import Neo4jClient

from bim_graph.config import NODES_CSV, EDGES_CSV, DEFAULT_IFC_PATH
from bim_graph import load_to_neo4j, clash_pipeline
from extract_graph import run_extraction

router = APIRouter()

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


# ------------------------------------------------------------------
# RAG module discovery
# ------------------------------------------------------------------

_RAG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag")
if _RAG_DIR not in sys.path:
    sys.path.insert(0, _RAG_DIR)

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
            from chunker import chunk_pdf as _chunk_pdf
            _RAG_CHUNKER = _chunk_pdf
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"chunker import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_EMBEDDER is None:
        try:
            from embedder import embed_and_store as _embed_and_store
            from embedder import get_client_db as _get_client_db
            from embedder import get_or_create_collection as _get_or_create_collection
            from embedder import CHROMA_DIR as _CHROMA_DIR
            from embedder import COLLECTION_NAME as _COLLECTION_NAME
            _RAG_EMBEDDER = {
                "embed_and_store": _embed_and_store,
                "get_client_db": _get_client_db,
                "get_or_create_collection": _get_or_create_collection,
                "CHROMA_DIR": _CHROMA_DIR,
                "COLLECTION_NAME": _COLLECTION_NAME,
            }
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"embedder import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_RETRIEVER is None:
        try:
            from retriever import answer_question as _answer_question
            _RAG_RETRIEVER = _answer_question
        except Exception as exc:
            _RAG_IMPORT_ERROR = f"retriever import failed: {exc}"
            raise HTTPException(501, _RAG_IMPORT_ERROR)

    if _RAG_ORCHESTRATOR is None:
        try:
            from orchestrator import RAGOrchestrator as _RAGOrchestrator
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
def analyze():
    summary = clash_pipeline.run_clash_detection()
    return summary


@router.post("/ifc/upload")
def upload_ifc(
    file: UploadFile = File(..., description="IFC file to upload and scan for storeys/types"),
):
    """
    Save an uploaded .ifc file into dataset/ifc/ and scan *only that file*
    for storeys/types (not the whole directory), so the pipeline form's
    filters reflect exactly the file the user picked.
    """
    if not file.filename or not file.filename.lower().endswith(".ifc"):
        raise HTTPException(400, "Only .ifc files are accepted.")

    from extract_sotreys_type import DEFAULT_IFC_DIR, main as _extract_main

    os.makedirs(DEFAULT_IFC_DIR, exist_ok=True)
    dest_path = os.path.join(DEFAULT_IFC_DIR, file.filename)

    try:
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as exc:
        raise HTTPException(500, f"Failed to save uploaded IFC file: {exc}")
    finally:
        file.file.close()

    try:
        _extract_main(dest_path, storeys_csv=STOREYS_CSV, types_csv=TYPES_CSV)
    except Exception as exc:
        raise HTTPException(500, f"File saved but failed to scan it: {exc}")

    storeys, types, errors = _read_filter_csvs()

    return {
        "status": "ok",
        "filename": file.filename,
        "ifc_path": dest_path,
        "storeys": storeys,
        "types": types,
        "errors": errors,
    }


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


def _run_issue_query(issue_type, storey, types):
    """
    Shared Cypher for /clashes, /violations, /issues. Property names here
    must match what load_to_neo4j.py / clash_pipeline.py actually write:
    r.issue (not r.issue_type), a.ifcType/b.ifcType (not ifc_type),
    a.storeyName/b.storeyName (not storey_name).
    """
    with Neo4jClient() as client:
        where_clauses = []
        params = {}
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
        where_string = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
        MATCH (a:Element)-[r:CLASHES_WITH]->(b:Element)
        {where_string}
        RETURN a.ifcType AS a_type, a.name AS a_name,
               b.ifcType AS b_type, b.name AS b_name,
               r.issue AS issue, r.metric AS metric
        ORDER BY r.metric DESC
        LIMIT 1000
        """
        records = client.run(query, params)
        return [dict(r) for r in records]


@router.get("/clashes")
def get_clashes_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        return _run_issue_query("CLASH", storey, types)
    except Exception as e:
        print(f"[routes] /clashes failed: {e}", file=sys.stderr)
        return []


@router.get("/violations")
def get_violations_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        return _run_issue_query("CLEARANCE_VIOLATION", storey, types)
    except Exception as e:
        print(f"[routes] /violations failed: {e}", file=sys.stderr)
        return []


@router.get("/issues")
def get_issues_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        return _run_issue_query(None, storey, types)
    except Exception as e:
        print(f"[routes] /issues failed: {e}", file=sys.stderr)
        return []


# ------------------------------------------------------------------
# RAG corpus management: upload / ingest / status / clear
# ------------------------------------------------------------------

@router.post("/rag/upload")
def upload_pdf(
    file: UploadFile = File(..., description="PDF file to chunk, embed, and store in ChromaDB"),
    doc_id: Optional[str] = Query(None, description="Document ID prefix for chunk IDs (defaults to filename stem)"),
):
    _ensure_rag()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    if not doc_id:
        doc_id = Path(file.filename).stem

    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        chunks = _RAG_CHUNKER(tmp_path, doc_id=doc_id)
        if not chunks:
            raise HTTPException(400, "No text could be extracted from the PDF.")

        stored_count = _RAG_EMBEDDER["embed_and_store"](chunks)

        return {
            "status": "ok",
            "filename": file.filename,
            "doc_id": doc_id,
            "chunks_extracted": len(chunks),
            "chunks_stored": stored_count,
            "chroma_dir": _RAG_EMBEDDER["CHROMA_DIR"],
            "collection": _RAG_EMBEDDER["COLLECTION_NAME"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"PDF processing failed: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/rag/ingest")
def ingest_pdf_from_path(
    pdf_path: str = Query(..., description="Absolute or relative path to an existing PDF file"),
    doc_id: Optional[str] = Query(None, description="Document ID prefix for chunk IDs (defaults to filename stem)"),
):
    _ensure_rag()

    if not os.path.exists(pdf_path):
        raise HTTPException(404, f"PDF file not found: {pdf_path}")

    if not pdf_path.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    if not doc_id:
        doc_id = Path(pdf_path).stem

    try:
        chunks = _RAG_CHUNKER(pdf_path, doc_id=doc_id)
        if not chunks:
            raise HTTPException(400, "No text could be extracted from the PDF.")

        stored_count = _RAG_EMBEDDER["embed_and_store"](chunks)

        return {
            "status": "ok",
            "pdf_path": pdf_path,
            "doc_id": doc_id,
            "chunks_extracted": len(chunks),
            "chunks_stored": stored_count,
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
        result = orchestrator.ask(req.question)
    except Exception as exc:
        raise HTTPException(500, f"RAG pipeline error: {exc}")

    return result


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