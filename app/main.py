import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.database import engine as database_engine, init_db
from app.plugins import load_plugins
from app.services.embedding_engine import embedding_engine

# 1. IMPORT CÁC ROUTER Ở ĐÂY
from app.api.v1 import reading_room
from app.api.v1 import indicating_room
from app.api.v1 import admin_portal
from app.api.v1 import workflow
from app.api.v1 import pipeline_v2
# from app.api.v1 import matraix_room  # Bật lên khi cần dùng simulation

# 2. LIFESPAN EVENT (Khởi tạo DB lúc startup)
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_plugins()
    await init_db()
    print("[*] Database initialized. Ready to rock.")
    yield
    await embedding_engine.close()
    await database_engine.dispose()
    print("[*] Server shutting down...")

# 3. KHỞI TẠO BIẾN APP Ở ĐÂY TRƯỚC (QUAN TRỌNG VÃI NỒI)
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# 4. CẤU HÌNH MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. GẮN CÁC ROUTER API VÀO
app.include_router(reading_room.router, prefix=settings.API_V1_STR)
app.include_router(indicating_room.router, prefix=settings.API_V1_STR)
app.include_router(admin_portal.router, prefix=settings.API_V1_STR)
app.include_router(workflow.router, prefix=settings.API_V1_STR)
app.include_router(pipeline_v2.router, prefix=settings.API_V1_STR)
# app.include_router(matraix_room.router, prefix=settings.API_V1_STR)

# 6. MOUNT STATIC FILES & JINJA2 TEMPLATES
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Mount dedicated frontend distribution assets if available
FRONTEND_DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/dist"))
if os.path.exists(os.path.join(FRONTEND_DIST_DIR, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="frontend_assets")

def _serve_frontend_or_template(request: Request, template_name: str, context: Dict[str, Any] = None):
    dist_index = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if os.path.exists(dist_index):
        return FileResponse(dist_index)
    return templates.TemplateResponse(request=request, name=template_name, context=context or {})

# 7. ROUTE KIỂM TRA SỨC KHỎE
@app.get("/health", tags=["Health Check"])
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "llm_provider": settings.LLM_PROVIDER,
        "model": settings.LOCAL_MODEL_NAME if settings.LLM_PROVIDER == "local_ollama" else settings.CLOUD_MODEL_NAME,
        "embedding": embedding_engine.provider_status(),
    }

# 8. CÁC ROUTE GIAO DIỆN FRONTEND (MODULAR SPA + FALLBACK TEMPLATES)
@app.get("/", response_class=HTMLResponse, tags=["Frontpage"])
async def frontpage(request: Request):
    return _serve_frontend_or_template(request, "index.html")

@app.get("/reading-room", response_class=HTMLResponse, tags=["Frontpage"])
async def reading_room_page(request: Request):
    return _serve_frontend_or_template(request, "reading_room.html")

@app.get("/articles", response_class=HTMLResponse, tags=["Frontpage"])
async def articles_page(request: Request):
    return _serve_frontend_or_template(request, "reading_room.html")

@app.get("/indicating-room", response_class=HTMLResponse, tags=["Frontpage"])
async def indicating_room_page(request: Request):
    return _serve_frontend_or_template(request, "indicating_room.html")

@app.get("/tool", response_class=HTMLResponse, tags=["Frontpage"])
async def tool_page(request: Request):
    return _serve_frontend_or_template(request, "indicating_room.html")

@app.get("/analysis-room", response_class=HTMLResponse, tags=["Frontpage"])
async def analysis_room_page(request: Request):
    return _serve_frontend_or_template(request, "analysis_room.html")

@app.get("/co-scientist-lab", response_class=HTMLResponse, tags=["Frontpage"])
async def co_scientist_lab_page(request: Request):
    return _serve_frontend_or_template(request, "co_scientist_lab.html")

@app.get("/pipeline-v2", response_class=HTMLResponse, tags=["Frontpage"])
async def pipeline_v2_page(request: Request):
    return _serve_frontend_or_template(request, "pipeline_v2_guide.html")

@app.get("/about", response_class=HTMLResponse, tags=["Frontpage"])
async def about_page(request: Request):
    return _serve_frontend_or_template(request, "about.html")

@app.get("/admin", response_class=HTMLResponse, tags=["Frontpage"])
async def admin_portal_page(request: Request):
    return _serve_frontend_or_template(request, "admin.html")


