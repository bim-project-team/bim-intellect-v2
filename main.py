import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from api.routes import router as api_router

app = FastAPI(title="BIM-Intellect MVP")

# Resolve paths relative to this file's own location, not the process's
# current working directory. Depending on how uvicorn is launched (IDE run
# config, different shell cwd, etc.) "static"/"templates" as bare relative
# paths can silently 404 even though the app boots fine and every /api
# route works — that shows up as "styles aren't applied" with no error.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(_BASE_DIR, "templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _request: Request, exc: RequestValidationError
):
    """Return serializable validation details even when input is UploadFile."""
    details = []
    for error in exc.errors():
        # UploadFile and a few other request objects cannot be JSON encoded by
        # older FastAPI versions.  Keep only the stable, JSON-safe fields; the
        # raw input is unnecessary and can also contain sensitive request data.
        details.append({key: error[key] for key in ("type", "loc", "msg") if key in error})
    return JSONResponse(status_code=422, content={"detail": details})

# Mount our API routes strictly under the /api prefix
app.include_router(api_router, prefix="/api")

# Mount frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_frontend():
    # The frontend HTML actually lives in templates/index.html
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    # fallback to just serving text if missing so the backend still boots
    return {"message": f"BIM-Intellect Backend is running. Frontend missing from {index_path}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
