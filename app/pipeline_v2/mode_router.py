"""S4c mode router.

The user-selected mode always wins.  The classifier is advisory and is only
consulted for ``--mode auto``.  A ``mixed`` label or a confidence below the
calibrated threshold requires an explicit mode unless the caller allowed the
dual fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from app.pipeline_v2.config import Calibration
from app.pipeline_v2.contracts import (
    ClassifierResult,
    EffectiveMode,
    RequestedMode,
)
from app.pipeline_v2.errors import ModeSelectionRequired


@dataclass
class ModeDecision:
    mode: EffectiveMode
    source: str
    warnings: List[str] = field(default_factory=list)


def resolve_mode(
    requested_mode: RequestedMode,
    classifier: ClassifierResult,
    calibration: Calibration,
    *,
    allow_dual: bool = False,
) -> ModeDecision:
    """Resolve the effective engine mode.

    Explicit ``macro``/``micro``/``dual`` values are returned untouched.  For
    ``auto`` the calibrated classifier recommendation is used when it is
    confident; ``mixed`` and low-confidence inputs become ``dual`` only when
    ``allow_dual`` is true.
    """
    if requested_mode in {"macro", "micro", "dual"}:
        return ModeDecision(
            mode=requested_mode,  # type: ignore[arg-type]
            source="explicit",
        )

    if requested_mode != "auto":
        raise ModeSelectionRequired(
            f"Chế độ không hợp lệ: {requested_mode!r}.",
            suggestion="Dùng --mode macro, --mode micro, --mode dual hoặc --mode auto.",
        )

    threshold = float(calibration.threshold)
    low_confidence = classifier.confidence < threshold
    mixed = classifier.label == "mixed"

    if mixed or low_confidence:
        if allow_dual:
            return ModeDecision(
                mode="dual",
                source="auto_dual",
                warnings=[
                    "Bộ phân loại trả về tín hiệu hỗn hợp hoặc độ tin cậy thấp; "
                    "--allow-dual đã bật nên pipeline chạy cả macro và micro."
                ],
            )
        reason = (
            "đầu vào được phân loại là mixed"
            if mixed
            else f"độ tin cậy {classifier.confidence:.3f} thấp hơn ngưỡng {threshold:.3f}"
        )
        raise ModeSelectionRequired(
            f"Không thể chọn chế độ tự động vì {reason}.",
            suggestion=(
                "Chọn rõ --mode macro, --mode micro hoặc --mode dual; "
                "hoặc thêm --allow-dual nếu muốn chấp nhận chạy cả hai."
            ),
        )

    recommendation = classifier.routing_recommendation or classifier.label
    if recommendation == "mixed":
        recommendation = "dual"
    if recommendation not in {"macro", "micro", "dual"}:
        raise ModeSelectionRequired(
            "Bộ phân loại không đưa ra được chế độ hợp lệ.",
            suggestion="Chọn rõ --mode macro, --mode micro hoặc --mode dual.",
        )
    return ModeDecision(
        mode=recommendation,  # type: ignore[arg-type]
        source="classifier",
    )


async def dispatch_modes(
    decision: ModeDecision,
    *,
    run_macro: Callable[[], Awaitable[object]],
    run_micro: Callable[[object], Awaitable[object]],
) -> tuple[Optional[object], Optional[object]]:
    """Run macro/micro in dependency order.

    ``run_micro`` receives the macro bundle.  In dual mode the micro stage is
    skipped when the macro stage raises, so a failed macro run can never be
    silently replaced by a micro answer.
    """
    macro_bundle = None
    micro_bundle = None
    if decision.mode in {"macro", "dual"}:
        macro_bundle = await run_macro()
    if decision.mode == "micro":
        micro_bundle = await run_micro(None)
    elif decision.mode == "dual":
        if macro_bundle is None:
            return None, None
        micro_bundle = await run_micro(macro_bundle)
    return macro_bundle, micro_bundle


__all__ = ["ModeDecision", "dispatch_modes", "resolve_mode"]
