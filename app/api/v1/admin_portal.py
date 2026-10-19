from fastapi import APIRouter, Depends, BackgroundTasks
from app.core.security import verify_admin
from app.scrapers.scraper_worker import scraper_orchestrator

router = APIRouter(prefix="/admin", tags=["Admin Portal & Scraper Control"])

@router.post("/trigger-scraper", dependencies=[Depends(verify_admin)])
async def trigger_manual_crawl(background_tasks: BackgroundTasks):
    """Kích hoạt bot Playwright cào dữ liệu chạy ngầm trong BackgroundTasks"""
    background_tasks.add_task(scraper_orchestrator.run_daily_pipeline)
    return {"message": "Đã ra lệnh cho bot Chromium Playwright khởi chạy ngầm!"}