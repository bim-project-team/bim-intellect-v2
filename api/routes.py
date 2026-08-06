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
from pydantic import BaseModel

from bim_graph.neo4j_client import Neo4jClient

from bim_graph.config import NODES_CSV, EDGES_CSV, DEFAULT_IFC_PATH
from bim_graph import load_to_neo4j, clash_pipeline
from extract_graph import run_extraction

router = APIRouter()


class QuestionRequest(BaseModel):
    question: str


# ------------------------------------------------------------------
# RAG module discovery
#
# The rag/ package uses absolute intra-package imports (e.g.
# "from chunker import ..." instead of "from rag.chunker import ...").
# Those only resolve when rag/ itself is on sys.path. We add it
# dynamically here so the API can import them regardless of how the
# server was launched.
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
# IFC / Graph pipeline routes (unchanged)
# ------------------------------------------------------------------

@router.post("/extract")
def extract_ifc(
    ifc_path: str = Query(DEFAULT_IFC_PATH, description="Path to the .ifc file"),
    storey: Optional[List[str]] = Query(None, description="Repeatable: storey name(s) to include"),
    type: Optional[List[str]] = Query(None, description="Repeatable: IFC type(s) to include"),
):
    """
    Parse an IFC file (optionally scoped to specific storeys/types) into
    nodes.csv/edges.csv. Does NOT touch Neo4j - call /load separately
    (possibly multiple times, e.g. after a --reset) without re-parsing the
    IFC file each time.
    """
    if not os.path.exists(ifc_path):
        raise HTTPException(404, f"IFC file not found: {ifc_path}")

    node_rows, edge_rows = run_extraction(
        ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
        storey_filter=storey, type_filter=type,
    )

    return {
        "status": "ok",
        "ifc_path": ifc_path,
        "storey_filter": storey,
        "type_filter": type,
        "extracted_nodes": len(node_rows),
        "extracted_edges": len(edge_rows),
        "nodes_csv": NODES_CSV,
        "edges_csv": EDGES_CSV,
    }


@router.post("/load")
def load_into_neo4j(
    reset: bool = Query(False, description="Wipe the graph before loading"),
):
    """
    Bulk-load the current nodes.csv/edges.csv (from /extract) into Neo4j.
    Safe to call repeatedly against the same CSVs - node/edge writes are
    idempotent (MERGE-based), and --reset gives a clean slate on demand.
    """
    if not os.path.exists(NODES_CSV) or not os.path.exists(EDGES_CSV):
        raise HTTPException(
            409, f"{NODES_CSV}/{EDGES_CSV} not found - call /extract first."
        )
    return load_to_neo4j.load(nodes_csv=NODES_CSV, edges_csv=EDGES_CSV, reset=reset)


@router.post("/ingest")
def ingest_ifc(
    ifc_path: str = Query(DEFAULT_IFC_PATH, description="Path to the .ifc file"),
    storey: Optional[List[str]] = Query(None, description="Repeatable: storey name(s) to include"),
    type: Optional[List[str]] = Query(None, description="Repeatable: IFC type(s) to include"),
    reset: bool = Query(False, description="Wipe the graph before loading"),
):
    """
    Convenience wrapper: /extract followed by /load in one call. For
    iterating on the same IFC file/filter without re-parsing each time,
    call /extract once and /load as many times as you need instead.
    """
    if not os.path.exists(ifc_path):
        raise HTTPException(404, f"IFC file not found: {ifc_path}")

    node_rows, edge_rows = run_extraction(
        ifc_path, nodes_csv=NODES_CSV, edges_csv=EDGES_CSV,
        storey_filter=storey, type_filter=type,
    )
    load_summary = load_to_neo4j.load(nodes_csv=NODES_CSV, edges_csv=EDGES_CSV, reset=reset)

    return {
        "status": "ok",
        "ifc_path": ifc_path,
        "storey_filter": storey,
        "type_filter": type,
        "extracted_nodes": len(node_rows),
        "extracted_edges": len(edge_rows),
        "load_summary": load_summary,
    }


@router.post("/analyze")
def analyze():
    """
    Run AABB clash + clearance detection against whatever is currently
    loaded in Neo4j (call /ingest first), and persist results back as
    :CLASHES_WITH relationships.
    """
    summary = clash_pipeline.run_clash_detection()
    return summary


@router.get("/filters/storeys")
def get_available_storeys():
    """Dynamically searches the IFC graph for available storeys."""
    try:
        with Neo4jClient() as client:
            query = """
            MATCH (n) 
            WHERE n.storey_name IS NOT NULL AND n.storey_name <> "" 
            RETURN DISTINCT n.storey_name AS storey
            ORDER BY storey
            """
            records = client.run(query)
            return {"storeys": [r["storey"] for r in records]}
    except Exception as e:
        return {"storeys": [], "error": str(e)}

@router.get("/api/clashes")
def get_clashes_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        with Neo4jClient() as client:
            where_clauses = ["r.issue_type = 'CLASH'"]
            params = {}
            if storey:
                where_clauses.append("(a.storey_name = $storey OR b.storey_name = $storey)")
                params["storey"] = storey
            if types:
                type_list = types.split(",")
                where_clauses.append("(a.type IN $types OR b.type IN $types)")
                params["types"] = type_list
            where_string = f"WHERE {' AND '.join(where_clauses)}"
            
            query = f"""
            MATCH (a)-[r:CLASHES_WITH]->(b)
            {where_string}
            RETURN a.type AS a_type, a.name AS a_name,
                   b.type AS b_type, b.name AS b_name,
                   r.issue_type AS issue, r.metric AS metric
            LIMIT 1000
            """
            records = client.run(query, **params)
            return [dict(r) for r in records]
    except Exception as e:
        return []

@router.get("/api/violations")
def get_violations_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        with Neo4jClient() as client:
            where_clauses = ["r.issue_type = 'CLEARANCE_VIOLATION'"]
            params = {}
            if storey:
                where_clauses.append("(a.storey_name = $storey OR b.storey_name = $storey)")
                params["storey"] = storey
            if types:
                type_list = types.split(",")
                where_clauses.append("(a.type IN $types OR b.type IN $types)")
                params["types"] = type_list
            where_string = f"WHERE {' AND '.join(where_clauses)}"
            
            query = f"""
            MATCH (a)-[r:CLASHES_WITH]->(b)
            {where_string}
            RETURN a.type AS a_type, a.name AS a_name,
                   b.type AS b_type, b.name AS b_name,
                   r.issue_type AS issue, r.metric AS metric
            LIMIT 1000
            """
            records = client.run(query, **params)
            return [dict(r) for r in records]
    except Exception as e:
        return []

@router.get("/api/issues")
def get_issues_filtered(storey: Optional[str] = None, types: Optional[str] = None):
    try:
        with Neo4jClient() as client:
            where_clauses = []
            params = {}
            if storey:
                where_clauses.append("(a.storey_name = $storey OR b.storey_name = $storey)")
                params["storey"] = storey
            if types:
                type_list = types.split(",")
                where_clauses.append("(a.type IN $types OR b.type IN $types)")
                params["types"] = type_list
            where_string = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            query = f"""
            MATCH (a)-[r:CLASHES_WITH]->(b)
            {where_string}
            RETURN a.type AS a_type, a.name AS a_name,
                   b.type AS b_type, b.name AS b_name,
                   r.issue_type AS issue, r.metric AS metric
            LIMIT 1000
            """
            records = client.run(query, **params)
            return [dict(r) for r in records]
    except Exception as e:
        return []

@router.get("/clashes")
def get_clashes():
    """Hard clashes: overlapping bounding boxes."""
    rows = clash_pipeline.list_issues(issue="CLASH")
    return [dict(r) for r in rows]

@router.get("/violations")
def get_violations():
    """Clearance violations: elements closer than the minimum allowed gap."""
    rows = clash_pipeline.list_issues(issue="CLEARANCE_VIOLATION")
    return [dict(r) for r in rows]

@router.get("/issues")
def get_all_issues():
    """Both clashes and clearance violations together."""
    rows = clash_pipeline.list_issues(issue=None)
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# RAG corpus management: upload / ingest / status / clear
# ------------------------------------------------------------------

@router.post("/rag/upload")
def upload_pdf(
    file: UploadFile = File(..., description="PDF file to chunk, embed, and store in ChromaDB"),
    doc_id: Optional[str] = Query(None, description="Document ID prefix for chunk IDs (defaults to filename stem)"),
):
    """
    Upload a PDF via multipart/form-data, extract text chunks, generate
    embeddings via OpenRouter, and store them in the local ChromaDB
    collection. Safe to call multiple times - new chunks are additive.
    """
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
    """
    Ingest a PDF already on disk into ChromaDB. Same pipeline as
    /rag/upload but reads from a path instead of an HTTP upload.
    """
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
    """Return ChromaDB collection stats: document count, storage path, sample IDs."""
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
    """Delete the entire ChromaDB collection. Use with caution."""
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
    """
    Unified RAG endpoint. Automatically routes the question to:
    - Vector DB (Mabhas 15 regulations) if codes/laws are needed
    - Neo4j Graph (BIM elements & clashes) if building data is needed
    - Both if needed, combining into a single synthesized answer with citations.
    """
    _ensure_rag()

    orchestrator = _RAG_ORCHESTRATOR()
    try:
        result = orchestrator.ask(req.question)
    except Exception as exc:
        raise HTTPException(500, f"RAG pipeline error: {exc}")

    return result


@router.post("/ask-vector")
def ask_vector_only(req: QuestionRequest):
    """
    Debug endpoint: regulation vector search only (legacy retriever).
    Uses the same pipeline as before the graph integration.
    """
    _ensure_rag()

    try:
        return _RAG_RETRIEVER(req.question)
    except Exception as exc:
        raise HTTPException(500, f"Vector retrieval error: {exc}")


@router.post("/ask-graph")
def ask_graph_only(req: QuestionRequest):
    """Debug endpoint: Neo4j graph query only."""
    try:
        from bim_graph.graph_retriever import GraphRetriever
    except ImportError as exc:
        raise HTTPException(501, f"Graph retriever not available: {exc}")

    gr = GraphRetriever()
    try:
        return gr.ask(req.question)
    except Exception as exc:
        raise HTTPException(500, f"Graph query error: {exc}")