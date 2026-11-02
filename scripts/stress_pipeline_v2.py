#!/usr/bin/env python3
"""Reproducible S1-S7 stress matrix for pipeline v2.

The runner uses only synthetic Vietnamese context/household inputs, invokes the
real CLI in a subprocess, and records per-case JSON output, stderr and timing.
It never prints environment values or real personal data.

Usage:
    .venv/bin/python scripts/stress_pipeline_v2.py
    .venv/bin/python scripts/stress_pipeline_v2.py --output-dir /tmp/stress
    .venv/bin/python scripts/stress_pipeline_v2.py --skip-full-s6

Exit code is 0 only when every case matches its expected exit code.  The full
S6 case is the slowest; use ``--skip-full-s6`` for a quick S1-S5 matrix.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("/tmp/osint_pipeline_v2_stress")

BASE_QUERY = (
    "Lạm phát CPI, lãi suất và tỷ giá tác động đến thu nhập, "
    "chi tiêu và tiết kiệm hộ gia đình thế nào?"
)

CONTEXT_OPT_OUT = {
    "free_text": "Thu nhập 22 triệu, chi tiêu 18 triệu, tiết kiệm 18 triệu",
    "region": "Hà Nội",
    "occupation_sector": "giáo dục",
    "household_notes": "2 người lớn, 1 trẻ em",
    "assets_liabilities_notes": "Có khoản vay mua nhà, dư nợ 450 triệu",
    "persist_opt_in": False,
}

CONTEXT_OPT_IN = {
    "free_text": "Thu nhập 26 triệu, chi tiêu 20 triệu, tiết kiệm 24 triệu",
    "region": "Đà Nẵng",
    "occupation_sector": "dịch vụ",
    "household_notes": "2 người lớn, 2 trẻ em",
    "assets_liabilities_notes": "Không có khoản vay tiêu dùng",
    "persist_opt_in": True,
}

HOUSEHOLD = {
    "monthly_income_million_vnd": 22.0,
    "monthly_expenditure_million_vnd": 18.0,
    "budget_shares_w0": [0.45, 0.25, 0.12, 0.18],
    "n_adults": 2,
    "n_children": 1,
    "liquid_assets_million_vnd": 18.0,
    "illiquid_assets_million_vnd": 450.0,
    "monthly_debt_service_million_vnd": 3.2,
    "crra_rho": 2.5,
}


def _cases(paths: Dict[str, str], *, full_s6: bool) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = [
        {
            "name": "01_macro_persistent_skip_s6",
            "args": [
                "--mode",
                "macro",
                "--query",
                "Lạm phát CPI và chính sách tiền tệ tác động GDP thế nào?",
                "--skip-orchestrator",
            ],
        },
        {
            "name": "02_micro_persistent_optout",
            "args": [
                "--mode",
                "micro",
                "--query",
                "Giá thực phẩm tăng ảnh hưởng chi tiêu hộ gia đình tôi thế nào?",
                "--context-file",
                paths["context_opt_out"],
                "--household-file",
                paths["household"],
                "--skip-orchestrator",
            ],
        },
        {
            "name": "03_dual_persistent_optin",
            "args": [
                "--mode",
                "dual",
                "--query",
                BASE_QUERY,
                "--context-file",
                paths["context_opt_in"],
                "--household-file",
                paths["household"],
                "--skip-orchestrator",
            ],
        },
        {
            "name": "04_auto_macro_skip_s6",
            "args": [
                "--mode",
                "auto",
                "--query",
                "Ngân hàng Nhà nước điều chỉnh lãi suất điều hành và lạm phát CPI?",
                "--skip-orchestrator",
            ],
        },
        {
            "name": "05_auto_mixed_allow_dual",
            "args": [
                "--mode",
                "auto",
                "--query",
                BASE_QUERY,
                "--household-file",
                paths["household"],
                "--context-file",
                paths["context_opt_out"],
                "--allow-dual",
                "--skip-orchestrator",
            ],
        },
        {
            "name": "06_micro_missing_household",
            "args": [
                "--mode",
                "micro",
                "--query",
                "Thu nhập và chi tiêu hộ gia đình tôi thay đổi ra sao?",
                "--context-file",
                paths["context_opt_out"],
                "--skip-orchestrator",
            ],
        },
        {
            "name": "07_macro_human_output",
            "args": [
                "--mode",
                "macro",
                "--query",
                "Tỷ giá và dự trữ ngoại hối ảnh hưởng lạm phát thế nào?",
                "--output",
                "human",
                "--skip-orchestrator",
            ],
            "output": "human",
        },
        {
            "name": "08_auto_ambiguous_expected_error",
            "args": ["--mode", "auto", "--query", "Xin chào", "--dry-run"],
            "expected_exit": 2,
        },
        {
            "name": "09_dual_dry_run",
            "args": [
                "--mode",
                "dual",
                "--query",
                BASE_QUERY,
                "--context-file",
                paths["context_opt_out"],
                "--household-file",
                paths["household"],
                "--dry-run",
            ],
            "dry_run": True,
        },
    ]
    if full_s6:
        cases.append(
            {
                "name": "10_dual_full_s6",
                "args": [
                    "--mode",
                    "dual",
                    "--query",
                    BASE_QUERY,
                    "--context-file",
                    paths["context_opt_out"],
                    "--household-file",
                    paths["household"],
                    "--artifact-dir",
                    paths["artifacts"],
                ],
                "full_s6": True,
            }
        )
    return cases


def _write_inputs(output_dir: Path) -> Dict[str, str]:
    paths = {
        "context_opt_out": output_dir / "context_opt_out.json",
        "context_opt_in": output_dir / "context_opt_in.json",
        "household": output_dir / "household.json",
        "artifacts": output_dir / "artifacts",
    }
    paths["context_opt_out"].write_text(
        json.dumps(CONTEXT_OPT_OUT, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["context_opt_in"].write_text(
        json.dumps(CONTEXT_OPT_IN, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["household"].write_text(
        json.dumps(HOUSEHOLD, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["artifacts"].mkdir(parents=True, exist_ok=True)
    return {key: str(value) for key, value in paths.items()}


def _case_record(name: str, payload: Any, stderr: str) -> Dict[str, Any]:
    if not isinstance(payload, dict) or "run_id" not in payload:
        return {"run_id": None, "status": "non_json"}
    validation = payload.get("validation") or {}
    purge = validation.get("purge") or {}
    return {
        "run_id": payload.get("run_id"),
        "mode_used": payload.get("mode_used"),
        "status": payload.get("status"),
        "classifier_label": (payload.get("classifier") or {}).get("label"),
        "validation_passed": validation.get("passed"),
        "purge_outcome": purge.get("outcome"),
        "purge_committed": purge.get("committed"),
        "purge_removed": purge.get("entries_removed"),
        "purge_dry_run": purge.get("dry_run"),
        "orchestration": bool(payload.get("orchestration")),
        "micro_channels": len(
            (payload.get("micro") or {}).get("transmission_chains", [])
        ),
        "error_code": (payload.get("error") or {}).get("code"),
        "artifacts": payload.get("artifacts") or {},
        "stderr_tail": stderr.strip().splitlines()[-2:],
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for inputs, stdout/stderr and summary.json.",
    )
    parser.add_argument(
        "--skip-full-s6",
        action="store_true",
        help="Run only the nine S1-S5/error cases.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=None,
        help="Run only the named case(s); repeatable.",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _write_inputs(output_dir)
    cases = _cases(paths, full_s6=not args.skip_full_s6)
    if args.case:
        selected = set(args.case)
        cases = [case for case in cases if case["name"] in selected]
        if not cases:
            parser.error("--case did not match any case name")

    records: List[Dict[str, Any]] = []
    for case in cases:
        name = case["name"]
        stdout_path = output_dir / f"{name}.json"
        stderr_path = output_dir / f"{name}.err"
        command = [
            sys.executable,
            str(REPO_ROOT / "run_full_workflow.py"),
            *case["args"],
        ]
        started = time.monotonic()
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.run(
                command,
                cwd=REPO_ROOT,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
            )
        elapsed = round(time.monotonic() - started, 2)
        expected_exit = int(case.get("expected_exit", 0))
        try:
            payload = json.loads(stdout_path.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        record = {
            "case": name,
            "args": case["args"],
            "exit_code": process.returncode,
            "expected_exit": expected_exit,
            "duration_seconds": elapsed,
            "full_s6": bool(case.get("full_s6")),
            "dry_run": bool(case.get("dry_run")),
        }
        record.update(
            _case_record(
                name,
                payload,
                stderr_path.read_text(encoding="utf-8"),
            )
        )
        record["passed"] = process.returncode == expected_exit
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    summary = {
        "total_runs": len(records),
        "passed_runs": sum(1 for item in records if item["passed"]),
        "failed_runs": sum(1 for item in records if not item["passed"]),
        "runs": records,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"summary": str(summary_path)}))
    return 0 if summary["failed_runs"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
