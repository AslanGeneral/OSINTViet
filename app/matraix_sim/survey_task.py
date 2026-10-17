import asyncio
from typing import List, Dict
import aiohttp
from pydantic import BaseModel

class SurveyResponse(BaseModel):
    agent_id: int
    will_cut_spending: bool
    cut_category: str  # e.g., 'Dining Out', 'Entertainment', 'Education', 'None'
    sentiment_score: float  # -1.0 (cực kỳ bi quan) đến 1.0 (lạc quan)

class MatraixSurveyRunner:
    def __init__(self, personas: List[Dict], max_concurrency: int = 25):
        self.personas = personas
        # Dùng Semaphore để giới hạn concurrency, không làm sập server
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def _query_agent(self, session: aiohttp.ClientSession, persona: Dict, macro_event: str) -> SurveyResponse:
        async with self.semaphore:
            prompt = (
                f"You are a household decision maker with characteristics: {persona}.\n"
                f"Macroeconomic shock: {macro_event}.\n"
                "How do you adjust monthly budget? Answer in JSON format: "
                "{'will_cut_spending': bool, 'cut_category': str, 'sentiment_score': float}"
            )
            
            # Giả lập payload gọi LLM endpoint (Ollama local hoặc API)
            try:
                # Mockup logic nếu chưa gắn API key LLM thật:
                # Thay bằng session.post('http://localhost:11434/api/generate', ...)
                await asyncio.sleep(0.01) # Non-blocking mock latency
                
                # Logic phản hồi theo price_sensitivity của persona
                is_sensitive = persona.get("price_sensitivity") == "High"
                return SurveyResponse(
                    agent_id=persona["id"],
                    will_cut_spending=is_sensitive,
                    cut_category="Dining Out" if is_sensitive else "None",
                    sentiment_score=-0.65 if is_sensitive else 0.1
                )
            except Exception as e:
                return SurveyResponse(
                    agent_id=persona["id"],
                    will_cut_spending=False,
                    cut_category="Error",
                    sentiment_score=0.0
                )

    async def run_simulation(self, macro_event: str) -> List[SurveyResponse]:
        async with aiohttp.ClientSession() as session:
            tasks = [self._query_agent(session, p, macro_event) for p in self.personas]
            results = await asyncio.gather(*tasks)
            return results