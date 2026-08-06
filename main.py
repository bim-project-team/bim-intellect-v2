"""
FastAPI application entry point for BIM-Intellect.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.routes import router
from bim_graph.config import DEFAULT_IFC_PATH

app = FastAPI(title="BIM-Intellect API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
def dashboard(request: Request):
    """Serves the classic black-and-white UI for interacting with the API."""
    return templates.TemplateResponse(
        request, "index.html", {"default_ifc_path": DEFAULT_IFC_PATH}
    )


@app.get("/health")
def health():
    return {"status": "BIM-Intellect API running"}