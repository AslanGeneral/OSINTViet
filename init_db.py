# ============================================================
# init_db.py — File khởi tạo bảng duy nhất được commit [AR mục 1].
# File *.db không bao giờ được commit (đã nằm trong .gitignore).
# ============================================================
import asyncio
from app.core.database import init_db


if __name__ == "__main__":
    asyncio.run(init_db())
    print("[DB] Đã khởi tạo bảng từ SQLModel metadata.")
