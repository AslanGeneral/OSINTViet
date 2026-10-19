from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db

# 1. IMPORT CÁC ROUTER Ở ĐÂY
from app.api.v1 import reading_room
from app.api.v1 import indicating_room
from app.api.v1 import admin_portal
# from app.api.v1 import matraix_room  # Bật lên khi cần dùng simulation

# 2. LIFESPAN EVENT (Khởi tạo DB lúc startup)
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    print("[*] Database initialized. Ready to rock.")
    yield
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

# 5. BÂY GIỜ APP ĐÃ TỒN TẠI, GẮN CÁC ROUTER VÀO SAU
app.include_router(reading_room.router, prefix=settings.API_V1_STR)
app.include_router(indicating_room.router, prefix=settings.API_V1_STR)
app.include_router(admin_portal.router, prefix=settings.API_V1_STR)
# app.include_router(matraix_room.router, prefix=settings.API_V1_STR)

# 6. ROUTE KIỂM TRA SỨC KHỎE
@app.get("/health", tags=["Health Check"])
async def health_check():
    return {
        "status": "healthy",
        "llm_provider": settings.LLM_PROVIDER,
        "model": settings.LOCAL_MODEL_NAME if settings.LLM_PROVIDER == "local_ollama" else settings.CLOUD_MODEL_NAME
    }
