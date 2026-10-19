import hashlib
from typing import List, Dict
from bs4 import BeautifulSoup
from app.scrapers.base_crawler import BaseChromiumCrawler
from app.services.embedding_engine import embedding_engine

class MacroNewsScraper(BaseChromiumCrawler):
    # Cào chuyên mục Vĩ mô của VnEconomy làm mẫu
    TARGET_URL = "https://vneconomy.vn/tieu-diem.htm"

    async def scrape_and_filter_articles(self, limit: int = 5) -> List[Dict]:
        page = await self.get_page()
        valid_articles = []

        try:
            print("[*] Đang truy cập trang tin vĩ mô...")
            await page.goto(self.TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            content = await page.content()
            
            soup = BeautifulSoup(content, "html.parser")
            # Tìm các thẻ chứa bài viết tiêu điểm
            articles_tags = soup.find_all("article") or soup.find_all("div", class_="story")

            for tag in articles_tags[:limit]:
                title_tag = tag.find(["h2", "h3", "a"])
                link_tag = tag.find("a")
                desc_tag = tag.find("p") or tag.find("div", class_="story__summary")

                if not title_tag or not link_tag:
                    continue

                title = title_tag.get_text(strip=True)
                url = link_tag.get("href", "")
                if url.startswith("/"):
                    url = f"https://vneconomy.vn{url}"
                
                snippet = desc_tag.get_text(strip=True) if desc_tag else title

                # Tạo ID duy nhất bằng băm URL
                doc_id = hashlib.md5(url.encode()).hexdigest()

                # --- PIPELINE LỌC RÁC NGỮ NGHĨA BẰNG EMBEDDINGGEMMA-2 ---
                print(f"[*] Đang dùng EmbeddingGemma-2 thẩm định ngữ nghĩa: '{title[:40]}...'")
                is_macro = await embedding_engine.is_macro_relevant(f"{title}. {snippet}", threshold=0.55)

                if is_macro:
                    print(f"    -> [VALID] Bài viết liên quan vĩ mô! Đang index vào ChromaDB...")
                    # Tự động index vào Vector Store cho RAG của Reading Room
                    await embedding_engine.index_article(
                        article_id=doc_id,
                        text=f"{title}\n{snippet}",
                        metadata={"title": title, "url": url}
                    )
                    valid_articles.append({
                        "id": doc_id,
                        "title": title,
                        "url": url,
                        "snippet": snippet
                    })
                else:
                    print(f"    -> [SKIP] Bỏ qua bài rác không liên quan vĩ mô.")

            return valid_articles

        except Exception as e:
            print(f"[-] Lỗi cào tin tức: {str(e)}")
            return []
        finally:
            await page.close()
