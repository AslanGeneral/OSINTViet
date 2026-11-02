"""Command-line entry point for pipeline v2.

Conventions:
* machine-readable JSON goes to stdout;
* progress and structured errors go to stderr;
* no prompt is ever shown; a missing flag is an immediate structured error;
* the live article is opt-in (``--live``), so stress runs are deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.pipeline_v2.cache_log import CacheLogStore
from app.pipeline_v2.contracts import (
    HouseholdProfile,
    PersonalContext,
    PipelineOutput,
    RequestedMode,
)
from app.pipeline_v2.errors import (
    InputError,
    PipelineError,
    emit_structured_error,
    error_from_exception,
    redact_secrets,
)
from app.pipeline_v2.orchestrator import PipelineOrchestrator


@contextlib.contextmanager
def _stdout_to_stderr():
    """Keep incidental library/plugin prints out of machine-readable stdout."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _read_json_file(path: str, label: str) -> Any:
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise InputError(
            f"Không tìm thấy tệp {label}: {candidate}",
            suggestion=f"Kiểm tra đường dẫn --{label.replace('_', '-')}.",
        )
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except Exception as exc:
        raise InputError(
            f"Tệp {label} không phải JSON hợp lệ: {type(exc).__name__}: {exc}"
        ) from exc


def _load_context(path: Optional[str]) -> Optional[PersonalContext]:
    if not path:
        return None
    payload = _read_json_file(path, "context_file")
    if isinstance(payload, dict) and "personal_context" in payload:
        payload = payload["personal_context"]
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise InputError("context_file phải chứa một JSON object PersonalContext.")
    try:
        return PersonalContext(**payload)
    except Exception as exc:
        raise InputError(
            f"PersonalContext không hợp lệ: {exc}",
            suggestion=(
                "Chỉ dùng các trường free_text, region, occupation_sector, "
                "household_notes, assets_liabilities_notes, persist_opt_in."
            ),
        ) from exc


def _load_household(path: Optional[str]) -> Optional[HouseholdProfile]:
    if not path:
        return None
    payload = _read_json_file(path, "household_file")
    if isinstance(payload, dict) and "household_profile" in payload:
        payload = payload["household_profile"]
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise InputError("household_file phải chứa một JSON object HouseholdProfile.")
    try:
        return HouseholdProfile(**payload)
    except Exception as exc:
        raise InputError(f"HouseholdProfile không hợp lệ: {exc}") from exc


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "không xác định"
    return f"{value:.{digits}f}"


def _human_output(output: PipelineOutput) -> str:
    lines: List[str] = []
    lines.append(f"Run: {output.run_id}")
    lines.append(f"Chế độ: {output.mode_used}")
    status_vi = {
        "ok": "thành công",
        "ok_with_warnings": "thành công có cảnh báo",
        "failed": "thất bại",
    }.get(output.status, output.status)
    lines.append(f"Trạng thái: {status_vi}")

    if output.classifier is not None:
        cls = output.classifier
        lines.append(
            f"Phân loại: {cls.label} (tin cậy {cls.confidence:.3f}); "
            f"gợi ý {cls.routing_recommendation or cls.label}."
        )

    if output.macro is not None:
        lines.append("")
        lines.append("KẾT QUẢ VĨ MÔ")
        lines.append(output.macro.summary)
        for claim in output.macro.claims[:8]:
            lines.append(f"- {claim.text}")

    if output.micro is not None:
        lines.append("")
        lines.append("KẾT QUẢ VI MÔ")
        for chain in output.micro.transmission_chains:
            magnitude = (
                chain.magnitude_value
                if chain.magnitude_status in {"computed", "sourced"}
                else "not_estimated"
            )
            lines.append(
                f"- {chain.channel} [{chain.direction}] "
                f"({chain.magnitude_status}): {chain.mechanism}"
            )
            if magnitude:
                lines.append(f"  Quy mô: {magnitude}")
        if output.micro.assumptions:
            lines.append("Giả định:")
            lines.extend(f"  * {item}" for item in output.micro.assumptions)

    if output.validation is not None:
        lines.append("")
        lines.append("KIỂM TRA LẠI")
        for check in output.validation.checks:
            lines.append(
                f"- {check.name}: {'đạt' if check.passed else 'không đạt'}"
            )
            for item in check.failed_items:
                lines.append(f"  ! {item}")
        if output.validation.purge is not None:
            purge = output.validation.purge
            if purge.dry_run:
                lines.append(
                    f"Purge: dry-run, khớp {getattr(purge, 'entries_matched', 0)} "
                    "bản ghi verified."
                )
            elif purge.committed:
                lines.append(
                    f"Purge: đã xóa {purge.entries_removed} bản ghi; "
                    f"kết quả {purge.outcome}."
                )
            else:
                lines.append(f"Purge: {purge.outcome}.")

    if output.orchestration:
        lines.append("")
        lines.append("ORCHESTRATOR")
        lines.append(
            f"- viable={output.orchestration.get('viable_count')} "
            f"dead={output.orchestration.get('dead_count')} "
            f"model={output.orchestration.get('model')}"
        )

    if output.warnings:
        lines.append("")
        lines.append("CẢNH BÁO")
        lines.extend(f"- {item}" for item in output.warnings)

    if output.error is not None:
        lines.append("")
        lines.append(f"LỖI {output.error.code}: {output.error.message}")
        lines.append(f"Gợi ý: {output.error.suggestion}")

    if output.artifacts:
        lines.append("")
        lines.append("TỆP ARTIFACT")
        for key, value in sorted(output.artifacts.items()):
            if value:
                lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _quarantine_metadata(entry: Any) -> dict:
    # Deliberately omit ``content``: quarantine inspection must not dump raw
    # personal material to a terminal or log.
    return {
        "entry_id": entry.entry_id,
        "run_id": entry.run_id,
        "state": entry.state,
        "created_at": entry.created_at.isoformat(),
        "source_refs": list(entry.source_refs),
        "embedding_ref": entry.embedding_ref,
    }


async def _run_pipeline(args: argparse.Namespace) -> int:
    context = _load_context(args.context_file)
    household = _load_household(args.household_file)
    orchestrator = PipelineOrchestrator(
        artifact_dir=args.artifact_dir,
        model=args.model,
        include_orchestration_in_output=False,
    )
    print(
        f"[pipeline_v2] query={args.query[:80]!r} mode={args.mode} "
        f"dry_run={args.dry_run}",
        file=sys.stderr,
    )
    try:
        with _stdout_to_stderr():
            result = await orchestrator.run(
                query=args.query,
                requested_mode=args.mode,
                personal_context=context,
                household_profile=household,
                allow_dual=args.allow_dual,
                dry_run=args.dry_run,
                use_live=args.live,
                enable_orchestrator=not args.skip_orchestrator,
                byok_stdin=args.byok_stdin,
            )
    finally:
        try:
            from app.services.embedding_engine import embedding_engine

            with _stdout_to_stderr():
                await embedding_engine.close()
        except Exception:
            pass

    if args.output == "json":
        _json_print(result.output.model_dump(mode="json"))
    else:
        print(_human_output(result.output))
    if result.exit_code != 0:
        if result.output.error is not None:
            print(
                json.dumps(
                    result.output.error.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print(
                json.dumps(
                    {
                        "error": True,
                        "code": "PIPELINE_FAILED",
                        "message": "Pipeline thất bại.",
                        "suggestion": "Xem output JSON để biết chi tiết.",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
    print(
        f"[pipeline_v2] finished status={result.output.status} "
        f"exit={result.exit_code}",
        file=sys.stderr,
    )
    return result.exit_code


async def _quarantine_list(args: argparse.Namespace) -> int:
    with _stdout_to_stderr():
        store = CacheLogStore(persist=True)
        entries = await store.list_quarantined(args.run_id)
    payload = {
        "count": len(entries),
        "entries": [_quarantine_metadata(entry) for entry in entries],
    }
    if args.output == "json":
        _json_print(payload)
    else:
        if not entries:
            print("Không có bản ghi nào đang bị cách ly.")
        for entry in entries:
            print(
                f"{entry.entry_id} | run={entry.run_id} | "
                f"created={entry.created_at.isoformat()} | "
                f"refs={', '.join(entry.source_refs) or '-'}"
            )
    return 0


async def _quarantine_delete(args: argparse.Namespace) -> int:
    if not args.yes and not args.dry_run:
        raise InputError(
            "Thao tác xóa bản ghi cách ly cần cờ --yes để tránh xóa nhầm.",
            suggestion=(
                "Chạy quarantine-list trước, sau đó thêm --yes khi chắc chắn."
            ),
        )
    with _stdout_to_stderr():
        store = CacheLogStore(persist=True)
        report = await store.delete_quarantined(
            run_id=args.run_id, dry_run=args.dry_run
        )
    payload = report.model_dump(mode="json")
    payload["run_id"] = args.run_id or "*"
    if args.output == "json":
        _json_print(payload)
    else:
        if args.dry_run:
            print(
                "Dry-run: sẽ xóa "
                f"{getattr(report, 'entries_matched', 0)} bản ghi cách ly."
            )
        else:
            print(
                f"Đã xóa {report.entries_removed} bản ghi cách ly "
                f"(run={args.run_id or '*'})."
            )
    return 0


class _StructuredArgumentParser(argparse.ArgumentParser):
    """Turn usage errors into the same StructuredError contract as the CLI."""

    def error(self, message: str) -> None:
        raise InputError(
            f"Tham số dòng lệnh không hợp lệ: {message}",
            suggestion="Chạy --help để xem cách dùng lệnh.",
        )


def _add_run_parser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "run", help="Chạy pipeline v2 (macro, micro hoặc dual)."
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["macro", "micro", "dual", "auto"],
        help="Chế độ engine; auto dùng classifier.",
    )
    parser.add_argument("--query", required=True, help="Truy vấn người dùng.")
    parser.add_argument(
        "--context-file",
        default=None,
        help="Tệp JSON PersonalContext (không chứa API key).",
    )
    parser.add_argument(
        "--household-file",
        default=None,
        help="Tệp JSON HouseholdProfile với số liệu hộ tường minh.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Không purge, không ghi DB/artifact, không chạy S6.",
    )
    parser.add_argument(
        "--output", choices=["json", "human"], default="json"
    )
    parser.add_argument(
        "--artifact-dir", default=None, help="Thư mục artifact của run."
    )
    parser.add_argument(
        "--allow-dual",
        action="store_true",
        help="Cho phép auto chuyển sang dual khi classifier mixed/không chắc.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch bài báo live qua Chromium (mặc định: chỉ dùng truy vấn).",
    )
    parser.add_argument(
        "--skip-orchestrator",
        action="store_true",
        help="Bỏ S6 (dùng cho stress test S1-S5).",
    )
    parser.add_argument(
        "--byok-stdin",
        action="store_true",
        help="Đọc API key từ một dòng stdin; không bao giờ echo key.",
    )
    parser.add_argument("--model", default=None, help="Model LLM override.")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = _StructuredArgumentParser(
        description="OSINTViet pipeline v2 (macro | micro | dual)."
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_StructuredArgumentParser,
    )
    _add_run_parser(subparsers)

    quarantine_list = subparsers.add_parser(
        "quarantine-list", help="Liệt kê bản ghi cache đang bị cách ly."
    )
    quarantine_list.add_argument("--run-id", default=None)
    quarantine_list.add_argument(
        "--output", choices=["json", "human"], default="json"
    )

    quarantine_delete = subparsers.add_parser(
        "quarantine-delete", help="Xóa bản ghi cách ly (yêu cầu --yes)."
    )
    quarantine_delete.add_argument("--run-id", default=None)
    quarantine_delete.add_argument("--dry-run", action="store_true")
    quarantine_delete.add_argument("--yes", action="store_true")
    quarantine_delete.add_argument(
        "--output", choices=["json", "human"], default="json"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # When called as ``run_full_workflow.py --mode ...`` we can accept the run
    # flags without an explicit ``run`` subcommand.
    if args and args[0] not in {"run", "quarantine-list", "quarantine-delete"}:
        args.insert(0, "run")
    parser = build_parser()
    try:
        namespace = parser.parse_args(args)
        if namespace.command == "run":
            return asyncio.run(_run_pipeline(namespace))
        if namespace.command == "quarantine-list":
            return asyncio.run(_quarantine_list(namespace))
        if namespace.command == "quarantine-delete":
            return asyncio.run(_quarantine_delete(namespace))
        raise InputError(f"Lệnh không hỗ trợ: {namespace.command}")
    except PipelineError as exc:
        return emit_structured_error(exc)
    except KeyboardInterrupt:
        print("[pipeline_v2] interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        contract, exit_code = error_from_exception(exc)
        print(
            json.dumps(contract.model_dump(mode="json"), ensure_ascii=False),
            file=sys.stderr,
        )
        return exit_code


__all__ = ["build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
