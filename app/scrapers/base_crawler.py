import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

class BaseChromiumCrawler:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def init_browser(self):
        """Khởi tạo Chromium với stealth headers và tối ưu hóa tài nguyên"""
        p = await async_playwright().start()
        self.browser = await p.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu"
            ]
        )
        # Giả lập một phiên duyệt web thật sự của Chrome trên Linux
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="vi-VN"
        )
        
        # CHẶN TẢI HÌNH ẢNH / FONT / CSS RÁC ĐỂ TĂNG TỐC ĐỘ 
        await self.context.route(
            "**/*",
            lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_()
        )

    async def get_page(self) -> Page:
        if not self.context:
            await self.init_browser()
        return await self.context.new_page()

    async def close(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
