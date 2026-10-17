import hashlib
import json
import os
from typing import Type, TypeVar
import httpx
from pydantic import BaseModel
from app.core.config import settings

T = TypeVar("T", bound=BaseModel)

class LLMRoutingEngine:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.cache_dir = settings.CACHE_DIR
        if settings.ENABLE_LLM_CACHE:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cache_key(self, prompt: str, schema_name: str) -> str:
        payload = f"{prompt}:{schema_name}:{self.provider}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        """Ép LLM trả về Structured Output theo format Pydantic model"""
        cache_key = self._get_cache_key(prompt, response_model.__name__)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")

        # 1. Kiểm tra Cache trước
        if settings.ENABLE_LLM_CACHE and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                return response_model.model_validate(cached_data)

        # 2. Xây dựng System Prompt với JSON Schema
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        system_instruction = (
            "You are a macroeconomic intelligence analyst specialized in microeconomic translation. "
            "You MUST output valid JSON matching the following schema exactly. Do NOT wrap in markdown ticks:\n"
            f"{schema_json}"
        )

        # 3. Route request
        if self.provider == "local_ollama":
            raw_json = await self._call_ollama(system_instruction, prompt)
        else:
            raw_json = await self._call_cloud_api(system_instruction, prompt)

        # 4. Parse & Validate
        parsed_result = response_model.model_validate_json(raw_json)

        # 5. Lưu Cache
        if settings.ENABLE_LLM_CACHE:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(parsed_result.model_dump_json(indent=2))

        return parsed_result

    async def _call_ollama(self, system: str, user_content: str) -> str:
        """Gọi Ollama local endpoint trên Linux Mint"""
        url = f"{settings.LOCAL_OLLAMA_URL}/api/chat"
        payload = {
            "model": settings.LOCAL_MODEL_NAME,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content}
            ],
            "stream": False,
            "format": "json"  # Ép Ollama kích hoạt chế độ Constrained JSON Decoding
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                res = await client.post(url, json=payload)
                res.raise_for_status()
                data = res.json()
                return data["message"]["content"]
            except httpx.ConnectError:
                raise RuntimeError(
                    f"WTF bro! Ollama chưa chạy ở {settings.LOCAL_OLLAMA_URL}. "
                    "Gõ 'ollama serve' vào Terminal Linux Mint đi!"
                )

    async def _call_cloud_api(self, system: str, user_content: str) -> str:
        """Dự phòng gọi OpenAI-compatible API"""
        url = f"{settings.CLOUD_API_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.CLOUD_API_KEY}"}
        payload = {
            "model": settings.CLOUD_MODEL_NAME,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"}
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            data = res.json()
            return data["choices"][0]["message"]["content"]

llm_router = LLMRoutingEngine()