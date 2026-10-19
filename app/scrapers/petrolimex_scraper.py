import re
from datetime import datetime
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
from app.scrapers.base_crawler import BaseChromiumCrawler

class PetrolimexScraper(BaseChromiumCrawler):
    TARGET_URL = "https://www.petrolimex.com.vn/nd/gia-ban-le-xang-dau.html"

    async def scrape_latest_prices(self) -> Optional[Dict[str, Any]]:
        page = await self.get_page()
        try:
            print("[*] Đang truy cập Petrolimex để cào giá xăng...")
            await page.goto(self.TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            
            # Đợi bảng dữ liệu xuất hiện
            await page.wait_for_selector("table", timeout=10000)
            content = await page.content()
            
            soup = BeautifulSoup(content, "html.parser")
            table = soup.find("table")
            if not table:
                raise ValueError("Không tìm thấy thẻ table trên trang Petrolimex!")

            prices = {}
            for row in table.find_all("tr"):
                cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cols) >= 2:
                    name = cols[0].lower()
                    # Trích xuất số thực từ chuỗi giá (ví dụ: '23.550' -> 23550.0)
                    price_match = re.search(r"[\d\.,]+", cols[1])
                    if price_match:
                        raw_price_str = price_match.group(0).replace(".", "").replace(",", ".")
                        try:
                            clean_price = float(raw_price_str)
                            if "ron 95" in name:
                                prices["ron_95_iii"] = clean_price
                            elif "diesel" in name or "do" in name:
                                prices["diesel_005s"] = clean_price
                            elif "e5" in name:
                                prices["e5_ron_92"] = clean_price
                        except ValueError:
                            continue

            result = {
                "timestamp": datetime.now().strftime("%Y-%m-%d"),
                "source": "Petrolimex",
                "prices": prices
            }
            print(f"[+] Cào giá xăng thành công: {result['prices']}")
            return result

        except Exception as e:
            print(f"[-] Lỗi cào Petrolimex: {str(e)}")
            return None
        finally:
            await page.close()