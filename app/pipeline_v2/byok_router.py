"""BYOK router construction.

The user key is read from an environment variable (or explicitly from stdin
when the caller asks for it) and held only in this process.  It is never put in
config, cache entries, audit rows, error payloads or logs.  All error text that
leaves this module is passed through ``redact_secrets`` with the live key added
to the redaction set.

Without a key the module falls back to the repository's existing router
selection (local Ollama or the existing cloud pool).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.pipeline_v2.config import BYOKConfig, PipelineV2Config, load_pipeline_config
from app.pipeline_v2.errors import InputError, LLMError, redact_secrets


@dataclass
class RouterBundle:
    router: Any
    using_byok: bool = False
    warnings: List[str] = field(default_factory=list)
    # Only kept in memory so the orchestrator can redact unexpected messages.
    extra_secrets: List[str] = field(default_factory=list, repr=False)


class OpenAISingleKeyRouter:
    """Minimal OpenAI-compatible chat client for one in-memory key.

    ``JsonEnforcingRouter`` wraps this adapter, so schema validation and retry
    feedback stay in the existing code path.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        if not api_key:
            raise InputError("BYOK key rỗng.")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.last_model = model

    async def call_worker(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        preferred_model: Optional[str] = None,
        temperature: float = 0.4,
    ) -> str:
        model = preferred_model or self.model
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(temperature),
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                if response.status_code == 400:
                    # Some compatible gateways reject response_format.  Retry
                    # once without it; the JSON-only prompt still applies.
                    payload.pop("response_format", None)
                    response = await client.post(
                        url,
                        json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
                if response.status_code >= 400:
                    body = redact_secrets(
                        response.text[:300], [self._api_key]
                    )
                    raise LLMError(
                        f"BYOK endpoint trả về HTTP {response.status_code}: {body}",
                        suggestion=(
                            "Kiểm tra base URL, tên mô hình và quyền của API key; "
                            "không dán key vào log."
                        ),
                    )
                data = response.json()
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(
                "Không gọi được BYOK endpoint: "
                + redact_secrets(f"{type(exc).__name__}: {exc}", [self._api_key]),
                suggestion="Kiểm tra mạng, base URL và mô hình của bạn.",
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(
                "BYOK endpoint trả về JSON không có choices[0].message.content.",
                suggestion="Dùng endpoint tương thích OpenAI chat completions.",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("BYOK endpoint trả về nội dung rỗng.")
        self.last_model = str(data.get("model") or model)
        return content

    async def close(self) -> None:
        # Drop the only live reference held by this adapter.
        self._api_key = ""


def read_key_from_stdin(stream: Any = None) -> str:
    """Read exactly one key line from stdin; never echo it."""
    source = stream or sys.stdin
    line = source.readline()
    key = (line or "").strip()
    if not key:
        raise InputError(
            "Không đọc được API key từ stdin.",
            suggestion="Truyền một dòng key qua stdin khi dùng --byok-stdin.",
        )
    return key


def _resolve_key(
    cfg: BYOKConfig,
    *,
    use_stdin: bool,
    stdin_stream: Any = None,
) -> Optional[str]:
    if use_stdin:
        return read_key_from_stdin(stdin_stream)
    key = os.environ.get(cfg.api_key_env, "").strip()
    return key or None


def build_router(
    *,
    config: Optional[PipelineV2Config] = None,
    trace: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    use_stdin: bool = False,
    stdin_stream: Any = None,
) -> RouterBundle:
    """Return a strict JSON router, preferring the user's BYOK key if present."""
    cfg = config or load_pipeline_config()
    key = _resolve_key(cfg.byok, use_stdin=use_stdin, stdin_stream=stdin_stream)
    if not key:
        from app.services.json_router import build_orchestrator_router

        return RouterBundle(
            router=build_orchestrator_router(trace=trace, model=model),
            using_byok=False,
        )

    base_url = (
        os.environ.get(cfg.byok.base_url_env, "").strip()
        or cfg.byok.default_base_url
    )
    model_name = (
        os.environ.get(cfg.byok.model_env, "").strip()
        or model
        or cfg.byok.default_model
    )
    from app.services.json_router import JsonEnforcingRouter

    inner = OpenAISingleKeyRouter(
        api_key=key,
        base_url=base_url,
        model=model_name,
        timeout_seconds=cfg.byok.timeout_seconds,
    )
    return RouterBundle(
        router=JsonEnforcingRouter(inner, trace=trace),
        using_byok=True,
        warnings=[
            "BYOK đang bật: API key chỉ nằm trong bộ nhớ tiến trình và không "
            "được ghi vào cache, log hay cơ sở dữ liệu."
        ],
        extra_secrets=[key],
    )


__all__ = [
    "OpenAISingleKeyRouter",
    "RouterBundle",
    "build_router",
    "read_key_from_stdin",
]
