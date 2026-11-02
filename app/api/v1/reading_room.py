import json
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select
from app.schemas.macro_schema import MacroBreakdownResponse
from app.services.llm_engine import llm_router
from app.services.embedding_engine import embedding_engine
from app.core.database import async_session_factory
from app.models.article import Article

router = APIRouter(prefix="/reading-room", tags=["Phòng Đọc (Reading Room)"])


@router.get("/articles")
async def list_articles(
    limit: int = 20,
    offset: int = 0,
):
    """Lấy danh sách các bài viết phân tích mới nhất cho báo điện tử và trang chủ."""
    lim = int(limit.default if hasattr(limit, "default") else limit)
    off = int(offset.default if hasattr(offset, "default") else offset)
    async with async_session_factory() as session:
        statement = select(Article).order_by(Article.ingested_at.desc()).offset(off).limit(lim)
        result = await session.execute(statement)
        rows = list(result.scalars().all())

    articles_out = []
    for idx, row in enumerate(rows):
        # Ignore raw system artifacts if any
        if row.source.startswith("pipeline_v2_") and not row.raw_content:
            continue

        chapeau = None
        thumbnail = None
        if row.payload_json:
            try:
                payload = json.loads(row.payload_json)
                chapeau = payload.get("chapeau") or payload.get("summary") or None
                thumbnail = payload.get("thumbnail") or payload.get("image_url") or None
            except Exception:
                pass

        content_sample = (row.cleaned_content or row.raw_content or "").strip()
        if not chapeau and content_sample:
            chapeau = content_sample[:220].rsplit(" ", 1)[0] + "..." if len(content_sample) > 220 else content_sample

        articles_out.append({
            "id": row.id,
            "title": row.title,
            "source": row.source,
            "url": row.url,
            "thumbnail": thumbnail,
            "chapeau": chapeau,
            "content": content_sample,
            "relevance_score": row.relevance_score,
            "domain_tag": row.domain_tag,
            "ingested_at": row.ingested_at.isoformat() if row.ingested_at else "",
        })

    return articles_out


@router.get("/articles/{article_id}")
async def get_article_detail(article_id: str):
    """Lấy chi tiết một bài viết để đọc và phân tích AI."""
    async with async_session_factory() as session:
        row = await session.get(Article, article_id)
        if not row:
            raise HTTPException(status_code=404, detail="Bài viết không tồn tại!")

        chapeau = None
        thumbnail = None
        if row.payload_json:
            try:
                payload = json.loads(row.payload_json)
                chapeau = payload.get("chapeau") or payload.get("summary") or None
                thumbnail = payload.get("thumbnail") or payload.get("image_url") or None
            except Exception:
                pass

        content_full = (row.cleaned_content or row.raw_content or "").strip()
        if not chapeau and content_full:
            chapeau = content_full[:220].rsplit(" ", 1)[0] + "..." if len(content_full) > 220 else content_full

        return {
            "id": row.id,
            "title": row.title,
            "source": row.source,
            "url": row.url,
            "thumbnail": thumbnail,
            "chapeau": chapeau,
            "content": content_full,
            "relevance_score": row.relevance_score,
            "domain_tag": row.domain_tag,
            "ingested_at": row.ingested_at.isoformat() if row.ingested_at else "",
        }


class ArticleAnalyzeRequest(BaseModel):
    raw_article_text: str
    source_url: str = ""

@router.post("/breakdown", response_model=MacroBreakdownResponse)
async def translate_macro_to_micro(req: ArticleAnalyzeRequest):
    if len(req.raw_article_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Văn bản quá ngắn, vứt bài báo chuẩn vào đây!")

    prompt = (
        f"Analyze the following economic report/article:\n"
        f"--- START ARTICLE ---\n{req.raw_article_text}\n--- END ARTICLE ---\n"
        f"Explain this directly to a Vietnamese middle schooler and relate each macro variable "
        f"to concrete household expenses (food, gasoline, school tuition, debt)."
    )

    try:
        breakdown = await llm_router.generate_structured(prompt, MacroBreakdownResponse)
        return breakdown
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM Processing Error: {str(e)}")

class SearchQuery(BaseModel):
    query: str
    top_k: int = 3

@router.post("/semantic-search")
async def search_macro_articles(req: SearchQuery):
    """Semantic search"""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Nhập nội dung tìm kiếm vào!")
    
    results = await embedding_engine.semantic_search(req.query, top_k=req.top_k)
    return {"query": req.query, "results": results}

