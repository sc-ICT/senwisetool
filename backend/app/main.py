from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager
from app.core.config import settings
from app.api.v1.router import api_router
from app.db.database import engine, Base
import app.models  # Assure l'enregistrement de tous les modèles SQLAlchemy (dont Notification)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Configuration CORS pour autoriser l'échange de cookies HttpOnly avec Next.js
origins = [
    settings.FRONTEND_URL,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger("app.main")

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception(f"Unhandled exception during request {request.method} {request.url}: {exc}")
    origin = request.headers.get("origin")
    headers = {}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"detail": f"Erreur interne du serveur : {str(exc)}"},
        headers=headers
    )


from pathlib import Path
from fastapi.staticfiles import StaticFiles

# Inclusion des routes de l'API v1
app.include_router(api_router, prefix=settings.API_V1_STR)

# Montage du dossier de téléchargements de documents (Kbis, statuts, justificatifs)
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Montage du dossier de stockage structuré des médias (Multi-tenant & Plateforme)
STORAGE_ROOT = Path(settings.STORAGE_LOCAL_ROOT)
if not STORAGE_ROOT.is_absolute():
    STORAGE_ROOT = Path(__file__).resolve().parent.parent / settings.STORAGE_LOCAL_ROOT
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(STORAGE_ROOT)), name="storage")

@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.PROJECT_NAME}"}

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME}
