"""Strict JSON routers for the orchestrator.

Two adapters are provided:

* ``LocalOllamaJsonRouter`` uses Ollama's native ``/api/chat`` endpoint with a
  JSON-schema grammar.  This is the default when no cloud key pool is present.
* ``JsonEnforcingRouter`` wraps the existing ``SmartKeyPoolRouter`` and adds a
  JSON-only prompt, parsing/retry, Pydantic role validation, and an audit trace.

Every call is recorded as a JSON object (prompt hashes, parsed payload, schema
status).  The workflow stores this trace next to the final output so a reviewer
can see exactly which structured object moved through each stage.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.plugin_registry import hub
from app.core.smart_key_router import JUDGE_ROLES, SmartKeyPoolRouter
from app.schemas.universal_orchestrator_schema import (
    IngestedClusterSchema,
    MetricBoundSpec,
)
from app.schemas.workflow_schema import (
    ArticleAnalysis,
    JudgeVote,
    PracticalCritique,
    SandboxWorkerOutput,
    TheoreticalCritique,
    WorkerHypothesis,
)

JSON_ONLY_INSTRUCTION = (
    "Return exactly one JSON object. Do not use Markdown fences. "
    "Do not add explanations, prose, comments, or trailing text. "
    "Every required key must be present and every number must be finite."
)

ROLE_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "article_analyst": ArticleAnalysis,
    "structuring_worker": IngestedClusterSchema,
    "worker": WorkerHypothesis,
    "practical_critic": PracticalCritique,
    "theoretical_critic": TheoreticalCritique,
    "judge": JudgeVote,
    "judge_a": JudgeVote,
    "judge_b": JudgeVote,
    "sandbox_worker": SandboxWorkerOutput,
}

ROLE_MAX_TOKENS: Dict[str, int] = {
    "article_analyst": 900,
    "structuring_worker": 1_000,
    "worker": 700,
    "practical_critic": 600,
    "theoretical_critic": 600,
    "judge": 320,
    "judge_a": 320,
    "judge_b": 320,
    "sandbox_worker": 1_100,
}

_HYP_ID_RE = re.compile(r"gen_\d+_(?:hyp_\d+|hybrid_crossover)")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _excerpt(value: str, limit: int = 600) -> str:
    value = value or ""
    return value if len(value) <= limit else value[:limit] + "…"


def extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Extract one JSON object from a model response without inventing data."""
    if not raw:
        return None
    text = raw.strip()
    candidates = [text]
    cleaned = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidates.append(cleaned)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1])
        except Exception:
            return None
        if isinstance(value, dict):
            return value
    return None


def _extract_repair_ticket(user_prompt: str) -> Optional[Dict[str, Any]]:
    marker = "REPAIR_TICKET:"
    index = user_prompt.find(marker)
    if index < 0:
        return None
    rest = user_prompt[index + len(marker) :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(rest)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _validate_sandbox_code(
    code: str,
    user_prompt: str,
) -> Tuple[bool, str]:
    if not code or len(code) > 12_000:
        return False, "python_simulation_code is empty or too large"
    if "print(" not in code:
        return False, "python_simulation_code must print the final JSON line"
    if "json" not in code:
        return False, "python_simulation_code must use the json module"
    if "computed_metrics" not in code:
        return False, "python_simulation_code must emit computed_metrics"
    lowered = code.casefold()
    # Match NaN/Inf as standalone tokens.  A substring check would wrongly
    # reject ordinary variables such as ``financial_stability`` (which contains
    # the letters "nan") and ``inflation_rate`` (which contains "inf").
    if re.search(r"(?<![a-z0-9_])(nan|inf|infinity)(?![a-z0-9_])", lowered):
        return False, "python_simulation_code must not emit NaN/Infinity"
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return False, f"python_simulation_code has a syntax error: {exc}"

    ticket = _extract_repair_ticket(user_prompt) or {}
    bounds = ticket.get("metric_bounds") or []
    for bound in bounds:
        if not isinstance(bound, dict):
            continue
        metric_name = str(bound.get("metric_name") or "")
        if metric_name and metric_name not in code:
            return (
                False,
                f"python_simulation_code is missing metric key {metric_name!r}",
            )
    return True, ""


def _normalize_judge_vote(payload: Dict[str, Any], user_prompt: str) -> None:
    """Keep a judge call JSON-valid even when the model picks an unknown id.

    The orchestrator already treats an invalid/null winner as an abstention.
    Rewriting an unknown id to null preserves the JSON audit trail instead of
    turning the whole stage into a failed LLM call.
    """
    winner = payload.get("winner_id")
    if winner is None:
        return
    valid_ids = set(_HYP_ID_RE.findall(user_prompt))
    if not valid_ids or winner in valid_ids:
        return
    rationale = str(payload.get("rationale_vi") or "")
    mentioned = [hyp_id for hyp_id in valid_ids if hyp_id in rationale]
    payload["winner_id"] = mentioned[0] if len(mentioned) == 1 else None


def validate_role_payload(
    role: str,
    payload: Dict[str, Any],
    *,
    user_prompt: str = "",
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Validate and normalize one role payload.  Returns (payload, error)."""
    if not isinstance(payload, dict) or not payload:
        return None, "response is not a non-empty JSON object"

    model = ROLE_SCHEMAS.get(role)
    if model is None:
        return payload, ""

    try:
        validated = model.model_validate(payload)
    except ValidationError as exc:
        return None, f"{model.__name__} validation failed: {exc.errors()[:4]}"
    data = validated.model_dump(mode="json")

    if role == "structuring_worker":
        if not data.get("structured_facts_summary"):
            return None, "structured_facts_summary must not be empty"
        if not data.get("raw_excerpts"):
            return None, "raw_excerpts must not be empty"
        ontology = data.get("induced_ontology") or {}
        if not ontology.get("allowed_predicates"):
            return None, "induced_ontology.allowed_predicates must not be empty"

    if role == "worker":
        predicates = data.get("prolog_predicates") or {}
        if len(predicates) < 2:
            return None, "prolog_predicates must contain at least two numeric predicates"
        if any(not isinstance(v, (int, float)) for v in predicates.values()):
            return None, "prolog_predicates values must be numeric"

    if role == "theoretical_critic":
        # Keep only bounds that can be represented by MetricBoundSpec.  This is
        # a deterministic semantic filter; the JSON call itself remains valid.
        clean_bounds: List[Dict[str, Any]] = []
        for bound in data.get("enforced_sandbox_bounds") or []:
            try:
                spec = MetricBoundSpec.model_validate(bound)
            except ValidationError:
                continue
            if spec.operator == "in_range" and spec.upper_bound is None:
                continue
            if spec.upper_bound is not None and spec.upper_bound < spec.threshold_value:
                continue
            clean_bounds.append(spec.model_dump(mode="json"))
        data["enforced_sandbox_bounds"] = clean_bounds

    if role in {"judge", "judge_a", "judge_b"}:
        _normalize_judge_vote(data, user_prompt)

    if role == "sandbox_worker":
        ok, error = _validate_sandbox_code(
            str(data.get("python_simulation_code") or ""),
            user_prompt,
        )
        if not ok:
            return None, error

    return data, ""


@dataclass
class _LocalEndpoint:
    model: str
    name: str = "local_ollama"

    def available(self) -> bool:
        return True


@dataclass
class _TraceRecorder:
    records: List[Dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        call_id: str,
        role: str,
        model: str,
        attempt: int,
        final: bool,
        started_at: str,
        duration_ms: float,
        system_prompt: str,
        user_prompt: str,
        raw_response: str,
        parsed: Optional[Dict[str, Any]],
        parsed_ok: bool,
        schema_ok: bool,
        validation_error: str = "",
        transport_error: str = "",
    ) -> None:
        self.records.append(
            {
                "call_id": call_id,
                "role": role,
                "model": model,
                "attempt": attempt,
                "final": final,
                "started_at": started_at,
                "duration_ms": round(duration_ms, 1),
                "system_prompt_sha256": _sha256(system_prompt),
                "user_prompt_sha256": _sha256(user_prompt),
                "user_prompt_excerpt": _excerpt(user_prompt),
                "prompt_chars": len(system_prompt) + len(user_prompt),
                "response_chars": len(raw_response),
                "response_sha256": _sha256(raw_response),
                "response_excerpt": _excerpt(raw_response),
                "parsed_ok": parsed_ok,
                "schema_ok": schema_ok,
                "validation_error": validation_error,
                "transport_error": transport_error,
                "parsed": parsed,
            }
        )


class LocalOllamaJsonRouter:
    """Ollama native JSON-schema router with retries and an audit trace."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        trace: Optional[List[Dict[str, Any]]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.model = (model or settings.LOCAL_MODEL_NAME).strip()
        self.base_url = (base_url or settings.LOCAL_OLLAMA_URL).rstrip("/")
        self.timeout_seconds = float(
            timeout_seconds or settings.LOCAL_LLM_TIMEOUT_SECONDS
        )
        self.trace = _TraceRecorder(trace if trace is not None else [])
        self.endpoints = [_LocalEndpoint(model=self.model)]
        self._worker_models_in_use: set = set()
        self._judge_models_in_use: set = set()
        self.last_model = self.model
        import asyncio

        self._semaphore = asyncio.Semaphore(
            max(1, int(settings.LOCAL_LLM_CONCURRENCY))
        )

    async def warmup(self) -> Dict[str, Any]:
        """Load the model with the exact context size used by the workflow.

        The first cold inference on CPU can take several minutes.  A tiny
        one-token generation avoids that cold-start timeout without consuming
        another model slot or another context window.
        """
        payload = {
            "model": self.model,
            "prompt": "OK",
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.0,
                "num_ctx": int(settings.LOCAL_LLM_NUM_CTX),
                "num_predict": 1,
            },
        }
        url = f"{self.base_url}/api/generate"
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        self.last_model = self.model
        return {
            "model": self.model,
            "done": bool(data.get("done", True)),
            "done_reason": data.get("done_reason"),
            "load_duration_ns": data.get("load_duration"),
            "total_duration_ns": data.get("total_duration"),
            "num_ctx": int(settings.LOCAL_LLM_NUM_CTX),
        }

    def _system_prompt(self, role: str, system_prompt: str) -> str:
        parts = [system_prompt.strip(), JSON_ONLY_INSTRUCTION]
        if role == "structuring_worker":
            parts.append(
                "Required keys: cluster_id, crag_verification_state (one of "
                "CORRECT/AMBIGUOUS/INCORRECT), numerical_divergence_warning "
                "(object), structured_facts_summary (non-empty list of objects), "
                "raw_excerpts (non-empty list of strings), induced_ontology. "
                "induced_ontology must contain domain_tag, allowed_predicates "
                "(non-empty list of English snake_case strings), positive_rules "
                "and conflict_rules (lists of objects with rule_id, weight "
                "0.01-0.99, conditions, description_vi)."
            )
        if role == "theoretical_critic":
            parts.append(
                "Use English snake_case metric_name values. Return at most one "
                "enforced_sandbox_bound; it must be computable from a small "
                "standard-library simulation. Use 'in_range' only when "
                "upper_bound is also supplied. An empty list is allowed."
            )
        if role == "worker":
            parts.append(
                "prolog_predicates must map at least two English snake_case "
                "predicate names to finite numeric values between 0 and 1."
            )
        if role == "judge" or role in {"judge_a", "judge_b"}:
            parts.append(
                "winner_id must be exactly one of the hypothesis IDs in the "
                "prompt, or null when the evidence is insufficient."
            )
        if role == "sandbox_worker":
            parts.append(
                "The python_simulation_code field must contain a complete, "
                "self-contained Python program. Use only the standard library. "
                "It must compute a metric for EVERY metric_name in the "
                "REPAIR_TICKET metric_bounds, and the last printed line must be "
                'json.dumps({"computed_metrics": {...}, "passed": true, '
                '"all_bounds_satisfied": true}). Import json explicitly. '
                "Do not print NaN or Infinity."
            )
        return "\n\n".join(parts)

    async def _chat(
        self,
        *,
        role: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        schema: Any,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(role, system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": schema,
            "keep_alive": "10m",
            "options": {
                "temperature": float(temperature),
                "num_ctx": int(settings.LOCAL_LLM_NUM_CTX),
                "num_predict": ROLE_MAX_TOKENS.get(role, 700),
            },
        }
        url = f"{self.base_url}/api/chat"
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 400 and schema != "json":
                    # Some Ollama builds reject $ref-heavy schemas.  Keep the
                    # JSON-only guarantee and let Pydantic enforce the shape.
                    payload["format"] = "json"
                    response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        content = str((data.get("message") or {}).get("content") or "")
        if not content.strip():
            raise RuntimeError("Ollama returned an empty assistant message")
        self.last_model = self.model
        return content

    async def _generate_validated(
        self,
        *,
        role: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        schema: Any,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        call_id = uuid.uuid4().hex[:12]
        feedback = ""
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            started = _utc_now_iso()
            started_perf = time.perf_counter()
            effective_user = user_prompt
            if feedback:
                effective_user = (
                    f"{user_prompt}\n\nJSON VALIDATION FEEDBACK FROM PREVIOUS "
                    f"ATTEMPT: {feedback}\nReturn a corrected JSON object only."
                )
            raw = ""
            transport_error = ""
            try:
                raw = await self._chat(
                    role=role,
                    system_prompt=system_prompt,
                    user_prompt=effective_user,
                    temperature=temperature,
                    schema=schema,
                )
                parsed = extract_json_object(raw)
                if parsed is None:
                    raise ValueError("response could not be parsed as one JSON object")
                valid, error = validate_role_payload(
                    role, parsed, user_prompt=effective_user
                )
                if valid is None:
                    raise ValueError(error)
            except Exception as exc:
                transport_error = f"{type(exc).__name__}: {exc}"
                last_error = transport_error
                self.trace.record(
                    call_id=call_id,
                    role=role,
                    model=self.model,
                    attempt=attempt,
                    final=attempt >= max_attempts,
                    started_at=started,
                    duration_ms=(time.perf_counter() - started_perf) * 1000,
                    system_prompt=system_prompt,
                    user_prompt=effective_user,
                    raw_response=raw,
                    parsed=None,
                    parsed_ok=False,
                    schema_ok=False,
                    validation_error=last_error,
                    transport_error=transport_error,
                )
                feedback = last_error
                if attempt < max_attempts:
                    continue
                raise RuntimeError(
                    f"Structured LLM call failed for role {role}: {last_error}"
                ) from exc

            self.trace.record(
                call_id=call_id,
                role=role,
                model=self.model,
                attempt=attempt,
                final=True,
                started_at=started,
                duration_ms=(time.perf_counter() - started_perf) * 1000,
                system_prompt=system_prompt,
                user_prompt=effective_user,
                raw_response=raw,
                parsed=valid,
                parsed_ok=True,
                schema_ok=True,
            )
            if role in JUDGE_ROLES or role in {"judge_a", "judge_b"}:
                self._judge_models_in_use.add(self.model)
            else:
                self._worker_models_in_use.add(self.model)
            return valid

        raise RuntimeError(f"Structured LLM call failed for role {role}: {last_error}")

    async def call_worker(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        preferred_model: Optional[str] = None,
        temperature: float = 0.4,
    ) -> str:
        model = ROLE_SCHEMAS.get(role)
        schema = model.model_json_schema() if model else {"type": "object"}
        payload = await self._generate_validated(
            role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            schema=schema,
            max_attempts=2,
        )
        return json.dumps(payload, ensure_ascii=False)

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        role: str = "article_analyst",
        temperature: float = 0.1,
        max_attempts: int = 2,
    ) -> BaseModel:
        schema = response_model.model_json_schema()
        payload = await self._generate_validated(
            role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            schema=schema,
            max_attempts=max_attempts,
        )
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(
                f"Structured response failed {response_model.__name__}: {exc}"
            ) from exc

    async def close(self) -> None:
        return None


class JsonEnforcingRouter:
    """Cloud-router wrapper that enforces JSON + role validation + retries."""

    def __init__(
        self,
        inner: Any,
        trace: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.inner = inner
        self.trace = _TraceRecorder(trace if trace is not None else [])
        self.endpoints = getattr(inner, "endpoints", [])
        self._worker_models_in_use = getattr(inner, "_worker_models_in_use", set())
        self._judge_models_in_use = getattr(inner, "_judge_models_in_use", set())
        self.last_model = getattr(inner, "last_model", "")

    async def _generate_validated(
        self,
        *,
        role: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        call_id = uuid.uuid4().hex[:12]
        feedback = ""
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            started = _utc_now_iso()
            started_perf = time.perf_counter()
            effective_user = user_prompt
            if feedback:
                effective_user = (
                    f"{user_prompt}\n\nJSON VALIDATION FEEDBACK FROM PREVIOUS "
                    f"ATTEMPT: {feedback}\nReturn a corrected JSON object only."
                )
            raw = ""
            try:
                raw = await self.inner.call_worker(
                    role=role,
                    system_prompt=f"{system_prompt}\n\n{JSON_ONLY_INSTRUCTION}",
                    user_prompt=effective_user,
                    temperature=temperature,
                )
                parsed = extract_json_object(raw)
                if parsed is None:
                    raise ValueError("response could not be parsed as one JSON object")
                valid, error = validate_role_payload(
                    role, parsed, user_prompt=effective_user
                )
                if valid is None:
                    raise ValueError(error)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.trace.record(
                    call_id=call_id,
                    role=role,
                    model=getattr(self.inner, "last_model", ""),
                    attempt=attempt,
                    final=attempt >= max_attempts,
                    started_at=started,
                    duration_ms=(time.perf_counter() - started_perf) * 1000,
                    system_prompt=system_prompt,
                    user_prompt=effective_user,
                    raw_response=raw,
                    parsed=None,
                    parsed_ok=False,
                    schema_ok=False,
                    validation_error=last_error,
                    transport_error=last_error,
                )
                feedback = last_error
                if attempt < max_attempts:
                    continue
                raise RuntimeError(
                    f"Structured cloud LLM call failed for role {role}: {last_error}"
                ) from exc

            self.trace.record(
                call_id=call_id,
                role=role,
                model=getattr(self.inner, "last_model", ""),
                attempt=attempt,
                final=True,
                started_at=started,
                duration_ms=(time.perf_counter() - started_perf) * 1000,
                system_prompt=system_prompt,
                user_prompt=effective_user,
                raw_response=raw,
                parsed=valid,
                parsed_ok=True,
                schema_ok=True,
            )
            return valid
        raise RuntimeError(
            f"Structured cloud LLM call failed for role {role}: {last_error}"
        )

    async def call_worker(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        preferred_model: Optional[str] = None,
        temperature: float = 0.4,
    ) -> str:
        payload = await self._generate_validated(
            role=role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        return json.dumps(payload, ensure_ascii=False)

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        role: str = "article_analyst",
        temperature: float = 0.1,
        max_attempts: int = 2,
    ) -> BaseModel:
        schema_text = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False
        )
        payload = await self._generate_validated(
            role=role,
            system_prompt=(
                f"{system_prompt}\n\nThe required JSON schema is:\n{schema_text}"
            ),
            user_prompt=user_prompt,
            temperature=temperature,
            max_attempts=max_attempts,
        )
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(
                f"Structured response failed {response_model.__name__}: {exc}"
            ) from exc

    async def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _has_cloud_pool() -> bool:
    keys_file = Path("keys_pool.json")
    if keys_file.exists():
        try:
            payload = json.loads(keys_file.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, list) and payload:
            return True
    return bool(
        settings.GEMINI_API_KEYS.strip()
        or settings.EXTRA_API_KEYS.strip()
        or (settings.CLOUD_API_KEY.strip() and settings.CLOUD_MODEL_NAME.strip())
    )


def build_orchestrator_router(
    *,
    trace: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
):
    """Return a strict router: cloud pool when configured, local Ollama otherwise."""
    if _has_cloud_pool():
        return JsonEnforcingRouter(SmartKeyPoolRouter(), trace=trace)
    return LocalOllamaJsonRouter(model=model, trace=trace)


__all__ = [
    "JsonEnforcingRouter",
    "LocalOllamaJsonRouter",
    "build_orchestrator_router",
    "extract_json_object",
    "validate_role_payload",
]
