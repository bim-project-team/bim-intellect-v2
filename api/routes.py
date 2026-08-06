import os
from typing import Optional
from fastapi import APIRouter, File, Form, UploadFile, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from bim_graph.config import DEFAULT_IFC_PATH
from bim_graph.load_to_neo4j import load_graph
from bim_graph.clash_pipeline import run_clash_pipeline
from extract_graph import extract_graph
from rag.chunker import chunk_pdf
from rag.embedder import embed_documents
from rag.retriever import ChatSession
from rag.orchestrator import answer_question
from bim_graph.neo4j_client import Neo4jClient
import chromadb

router = APIRouter()
templates = Jinja2Templates(directory="templates")
chat_session = ChatSession()

@router.get("/")
def get_index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "default_ifc_path": DEFAULT_IFC_PATH
    })

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

@router.get("/api/results")
def get_results(storey: Optional[str] = None, types: Optional[str] = None):
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
            RETURN a.type AS type_a, a.name AS name_a,
                   b.type AS type_b, b.name AS name_b,
                   r.issue_type AS issue, r.metric AS metric
            LIMIT 1000
            """
            records = client.run(query, **params)
            return {"results": [dict(r) for r in records]}
    except Exception as e:
        return {"results": [], "error": str(e)}

@router.post("/api/ingest")
def ingest_model(ifc_path: str = Form(...), reset: bool = Form(True)):
    try:
        extract_graph(ifc_path)
        load_graph(reset=reset)
        run_clash_pipeline()
        return {"status": "success", "message": "Pipeline completed successfully."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/rag/upload")
async def upload_pdf(file: UploadFile = File(...), doc_id: str = Form("")):
    try:
        temp_path = f"/tmp/{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        embed_documents(temp_path)
        os.remove(temp_path)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

class AskRequest(BaseModel):
    question: str

@router.post("/api/ask")
def ask_question(req: AskRequest):
    try:
        # Use orchestrator for hybrid graph/vector RAG
        answer, sources = answer_question(req.question)
        chat_session.add_user_message(req.question)
        chat_session.add_assistant_message(answer, sources)
        return {"answer": answer, "sources": sources}
    except Exception as e:
        return {"error": str(e)}