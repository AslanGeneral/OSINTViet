"""Structured, secret-safe errors and CLI exit codes for pipeline v2."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Iterable, Optional, Sequence, TextIO

from app.pipeline_v2.contracts import StructuredError

# Stable process exit codes.  The CLI never prompts; callers can branch on
# these values in scripts.
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_INVALID_INPUT = 2
EXIT_EXTERNAL = 3
EXIT_ENGINE = 4
EXIT_VALIDATION = 5
EXIT_PURGE = 6

ERROR_EXIT_CODES = {
    "INVALID_INPUT": EXIT_INVALID_INPUT,
    "INVALID_CONTEXT": EXIT_INVALID_INPUT,
    "CONFIG_INVALID": EXIT_INVALID_INPUT,
    "CONFIG_MISSING": EXIT_INVALID_INPUT,
    "MODE_SELECTION_REQUIRED": EXIT_INVALID_INPUT,
    "MODE_INVALID": EXIT_INVALID_INPUT,
    "PERSONAL_CONTEXT_REQUIRED": EXIT_INVALID_INPUT,
    "UNSUPPORTED_INPUT": EXIT_INVALID_INPUT,
    "FETCH_FAILED": EXIT_EXTERNAL,
    "LLM_FAILED": EXIT_EXTERNAL,
    "EMBEDDING_FAILED": EXIT_EXTERNAL,
    "SOURCE_UNAVAILABLE": EXIT_EXTERNAL,
    "CLASSIFIER_FAILED": EXIT_ENGINE,
    "MACRO_FAILED": EXIT_ENGINE,
    "MICRO_FAILED": EXIT_ENGINE,
    "ENGINE_FAILED": EXIT_ENGINE,
    "CACHE_LOG_FAILED": EXIT_ENGINE,
    "REVALIDATION_FAILED": EXIT_VALIDATION,
    "PURGE_FAILED": EXIT_PURGE,
    "QUARANTINE_FAILED": EXIT_PURGE,
    "QUARANTINE_NOT_FOUND": EXIT_PURGE,
    "SECRET_DETECTED": EXIT_INVALID_INPUT,
    "INTERNAL_ERROR": EXIT_INTERNAL,
}

_SECRET_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSCODE",
    "PRIVATE_KEY",
)

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?i)\bkey\s*=\s*[A-Za-z0-9._\-]{12,}"),
)


def _environment_secrets() -> list[str]:
    secrets: list[str] = []
    for name, value in os.environ.items():
        upper = name.upper()
        if not value or len(value) < 8:
            continue
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            secrets.append(value)
    return secrets


def redact_secrets(
    text: object,
    extra_secrets: Optional[Iterable[str]] = None,
) -> str:
    """Remove API keys/tokens from any text before it reaches logs or JSON."""
    value = str(text)
    candidates: Sequence[str] = list(extra_secrets or ())
    for secret in list(candidates) + _environment_secrets():
        if secret and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class PipelineError(Exception):
    """A failure with a machine-readable code and a Vietnamese suggestion."""

    code = "INTERNAL_ERROR"
    exit_code = EXIT_INTERNAL
    default_suggestion = "Kiểm tra log lỗi và cấu hình rồi chạy lại."

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        suggestion: Optional[str] = None,
        exit_code: Optional[int] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.code = code or self.code
        self.message = message
        self.suggestion = suggestion or self.default_suggestion
        self.exit_code = (
            exit_code
            if exit_code is not None
            else ERROR_EXIT_CODES.get(self.code, EXIT_INTERNAL)
        )
        self.cause = cause
        super().__init__(self.message)

    def to_contract(
        self, extra_secrets: Optional[Iterable[str]] = None
    ) -> StructuredError:
        return StructuredError(
            code=self.code,
            message=redact_secrets(self.message, extra_secrets),
            suggestion=redact_secrets(self.suggestion, extra_secrets),
        )

    def __str__(self) -> str:  # pragma: no cover - debug convenience
        return f"{self.code}: {self.message}"


class ConfigError(PipelineError):
    code = "CONFIG_INVALID"
    default_suggestion = (
        "Bổ sung hoặc sửa tệp cấu hình trong config/ theo thông báo lỗi."
    )


class InputError(PipelineError):
    code = "INVALID_INPUT"
    default_suggestion = "Kiểm tra tham số dòng lệnh và nội dung tệp đầu vào."


class ModeSelectionRequired(PipelineError):
    code = "MODE_SELECTION_REQUIRED"
    default_suggestion = (
        "Chọn rõ --mode macro, --mode micro hoặc --mode dual; không dùng auto "
        "khi bộ phân loại không đủ tin cậy."
    )


class PersonalContextRequired(PipelineError):
    code = "PERSONAL_CONTEXT_REQUIRED"
    default_suggestion = (
        "Cung cấp --context-file với PersonalContext, hoặc dùng --mode macro."
    )


class FetchError(PipelineError):
    code = "FETCH_FAILED"
    default_suggestion = (
        "Kiểm tra kết nối mạng/Chromium, hoặc thử lại với --mode macro và "
        "nguồn dữ liệu đã lưu."
    )


class LLMError(PipelineError):
    code = "LLM_FAILED"
    default_suggestion = (
        "Kiểm tra mô hình cục bộ hoặc API key của bạn, sau đó chạy lại."
    )


class EmbeddingError(PipelineError):
    code = "EMBEDDING_FAILED"
    default_suggestion = (
        "Kiểm tra Ollama/Cohere và mô hình embedding trong .env."
    )


class EngineError(PipelineError):
    code = "ENGINE_FAILED"
    default_suggestion = "Kiểm tra đầu vào của engine và log chi tiết."


class ClassifierError(EngineError):
    code = "CLASSIFIER_FAILED"


class MacroEngineError(EngineError):
    code = "MACRO_FAILED"


class MicroEngineError(EngineError):
    code = "MICRO_FAILED"


class CacheLogError(EngineError):
    code = "CACHE_LOG_FAILED"


class RevalidationError(PipelineError):
    code = "REVALIDATION_FAILED"
    default_suggestion = (
        "Xem các mục failed_items trong báo cáo; dữ liệu chưa được purge."
    )


class PurgeError(PipelineError):
    code = "PURGE_FAILED"
    default_suggestion = (
        "Không xóa thủ công. Kiểm tra bảng cache_log_entries và audit gần nhất."
    )


class QuarantineError(PipelineError):
    code = "QUARANTINE_FAILED"
    default_suggestion = (
        "Dùng lệnh quarantine-list để xem các bản ghi đang bị cách ly."
    )


def error_from_exception(
    exc: BaseException,
    *,
    extra_secrets: Optional[Iterable[str]] = None,
) -> tuple[StructuredError, int]:
    """Convert any exception to ``(StructuredError, exit_code)``."""
    if isinstance(exc, PipelineError):
        return exc.to_contract(extra_secrets), exc.exit_code
    contract = StructuredError(
        code="INTERNAL_ERROR",
        message=redact_secrets(f"{type(exc).__name__}: {exc}", extra_secrets),
        suggestion="Xem log máy chủ; không có khóa bí mật nào được ghi ra.",
    )
    return contract, EXIT_INTERNAL


def emit_structured_error(
    exc: BaseException,
    *,
    stream: Optional[TextIO] = None,
    extra_secrets: Optional[Iterable[str]] = None,
) -> int:
    """Write one JSON error object to stderr and return the exit code."""
    contract, exit_code = error_from_exception(
        exc, extra_secrets=extra_secrets
    )
    target = stream or sys.stderr
    target.write(
        json.dumps(contract.model_dump(mode="json"), ensure_ascii=False) + "\n"
    )
    target.flush()
    return exit_code


__all__ = [
    "CacheLogError",
    "ClassifierError",
    "ConfigError",
    "ERROR_EXIT_CODES",
    "EXIT_ENGINE",
    "EXIT_EXTERNAL",
    "EXIT_INTERNAL",
    "EXIT_INVALID_INPUT",
    "EXIT_OK",
    "EXIT_PURGE",
    "EXIT_VALIDATION",
    "EmbeddingError",
    "EngineError",
    "FetchError",
    "InputError",
    "LLMError",
    "MacroEngineError",
    "MicroEngineError",
    "ModeSelectionRequired",
    "PersonalContextRequired",
    "PipelineError",
    "PurgeError",
    "QuarantineError",
    "RevalidationError",
    "emit_structured_error",
    "error_from_exception",
    "redact_secrets",
]
