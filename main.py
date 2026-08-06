import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import router as api_router

app = FastAPI(title="BIM-Intellect MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount our API routes WITHOUT a prefix, so routes.py handles the paths directly
app.include_router(api_router)

# Mount frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_frontend():
    # The frontend HTML actually lives in templates/index.html
    if os.path.exists("templates/index.html"):
        return FileResponse("templates/index.html")
    # fallback to just serving text if missing so the backend still boots
    return {"message": "BIM-Intellect Backend is running. Frontend missing from templates/index.html"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)