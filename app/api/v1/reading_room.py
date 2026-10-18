from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.schemas.macro_schema import MacroBreakdownResponse
from app.services.llm_engine import llm_router
from app.services.embedding_engine import embedding_engine

router = APIRouter(prefix="/reading-room", tags=["Phòng Đọc (Reading Room)"])

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
