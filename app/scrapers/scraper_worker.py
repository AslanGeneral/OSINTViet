import asyncio
from app.scrapers.petrolimex_scraper import PetrolimexScraper
from app.scrapers.macro_news_scraper import MacroNewsScraper

class ScraperOrchestrator:
    async def run_daily_pipeline(self):
        print("=== BẮT ĐẦU CHU KỲ CÀO DỮ LIỆU OSINT VĨ MÔ ===")
        petrol_crawler = PetrolimexScraper()
        news_crawler = MacroNewsScraper()

        try:
            # 1. Cào giá nhiên liệu
            fuel_data = await petrol_crawler.scrape_latest_prices()
            
            # 2. Cào và lọc tin tức vĩ mô qua EmbeddingGemma-2
            news_data = await news_crawler.scrape_and_filter_articles(limit=5)
            
            print(f"=== HOÀN TẤT: Cào được {len(news_data)} bài vĩ mô hợp lệ ===")
            return {
                "fuel_metrics": fuel_data,
                "ingested_articles": news_data
            }
        finally:
            await petrol_crawler.close()
            await news_crawler.close()

scraper_orchestrator = ScraperOrchestrator()