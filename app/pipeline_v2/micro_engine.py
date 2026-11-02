"""S4b micro channel engine.

The channel list is data in ``config/micro_channels.json``.  Each formula
handler below consumes explicit household inputs plus the read-only v5.7
artifact; when an input is missing the channel is ``not_estimated``.  No
household amount is ever invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.pipeline_v2.config import (
    ChannelList,
    HouseholdParameters,
    load_channel_list,
    load_household_parameters,
)
from app.pipeline_v2.contracts import (
    HouseholdProfile,
    MicroResult,
    MicroTransmissionChain,
    PersonalContext,
    RawMaterial,
)
from app.pipeline_v2.errors import MicroEngineError


@dataclass
class MicroBundle:
    result: MicroResult
    warnings: List[str] = field(default_factory=list)
    missing_inputs: List[str] = field(default_factory=list)
    evaluation: Optional[Dict[str, Any]] = None


def _fmt(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "không xác định"
    return f"{value:.{digits}f}"


def _macro_snapshot(macro_bundle: Any) -> Dict[str, Any]:
    if macro_bundle is None:
        return {}
    payload = getattr(macro_bundle, "micro_payload", {}) or {}
    return payload.get("macro_snapshot") or {}


def _channel(
    *,
    channel: str,
    direction: str,
    mechanism: str,
    magnitude_status: str,
    magnitude_value: Optional[str] = None,
    source_or_formula: Optional[str] = None,
) -> MicroTransmissionChain:
    return MicroTransmissionChain(
        channel=channel,
        direction=direction,  # type: ignore[arg-type]
        mechanism=mechanism,
        magnitude_status=magnitude_status,  # type: ignore[arg-type]
        magnitude_value=magnitude_value,
        source_or_formula=source_or_formula,
    )


class MicroChannelEngine:
    def __init__(
        self,
        params: Optional[HouseholdParameters] = None,
        channels: Optional[ChannelList] = None,
    ) -> None:
        self.params = params or load_household_parameters()
        self.channels = channels or load_channel_list()

    def _profile(
        self,
        household: Optional[HouseholdProfile],
        context: Optional[PersonalContext],
    ) -> HouseholdProfile:
        parsed = HouseholdProfile.from_personal_context(context)
        if household is None:
            return parsed
        return household.merged_over(parsed)

    def _run_bridge(
        self,
        profile: HouseholdProfile,
        macro_bundle: Any,
    ) -> Optional[Dict[str, Any]]:
        if macro_bundle is None:
            return None
        payload = getattr(macro_bundle, "micro_payload", None)
        if not payload:
            return None
        try:
            from app.math_engine.micro_household import (
                invoke_micro_household_subsystem,
            )
        except Exception as exc:
            raise MicroEngineError(
                "Không import được micro_household subsystem: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            return invoke_micro_household_subsystem(
                mode="single_individual_available",
                eco_engine_payload=payload,
                individual_profile=profile.model_dump(mode="python"),
                config_overrides=self.params.model_dump(mode="python"),
            )
        except Exception as exc:
            raise MicroEngineError(
                "Micro household subsystem thất bại: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Channel handlers
    # ------------------------------------------------------------------
    def _prices(
        self,
        profile: HouseholdProfile,
        evaluation: Optional[Dict[str, Any]],
        snapshot: Dict[str, Any],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        coli = (evaluation or {}).get("cost_of_living_evaluation")
        if coli:
            p_t = float(coli["expected_tornqvist_index_P_T"])
            cv = float(coli["expected_hicksian_cv_million_vnd"])
            direction = (
                "negative"
                if p_t > 1.0 + float(self.channels.direction_epsilon)
                else "positive"
                if p_t < 1.0 - float(self.channels.direction_epsilon)
                else "none"
            )
            return _channel(
                channel="prices",
                direction=direction,
                mechanism=(
                    "Cú sốc giá vĩ mô được chiếu vào 4 nhóm hàng tiêu dùng; "
                    "QUAIDS cập nhật tỷ trọng chi tiêu, Tornqvist-Theil đo chi phí "
                    "sinh hoạt và Hicks CV quy đổi ra triệu VND/tháng."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Chi phí sinh hoạt thay đổi {(p_t - 1.0) * 100:+.3f}% "
                    f"(P_T={p_t:.6f}); CV Hicks khoảng {cv:+.3f} triệu VND/tháng."
                ),
                source_or_formula=(
                    "M4/M5/M10 (Fisher/Tornqvist/Hicks CV); "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        cpi = (snapshot.get("core_series") or {}).get("CPI_YOY")
        if cpi is not None:
            return _channel(
                channel="prices",
                direction="negative" if float(cpi) > 0 else "none",
                mechanism=(
                    "CPI YoY hiện tại là "
                    f"{_fmt(float(cpi))}% theo artifact v5.7. Chưa có đủ "
                    "tỷ trọng chi tiêu và quy mô hộ để quy đổi thành số tiền "
                    "cụ thể cho hộ gia đình."
                ),
                magnitude_status="not_estimated",
            )
        return _channel(
            channel="prices",
            direction="ambiguous",
            mechanism=(
                "Chưa có artifact vĩ mô hoặc đủ dữ liệu giá để tính chi phí "
                "sinh hoạt cho hộ gia đình."
            ),
            magnitude_status="not_estimated",
        )

    def _income(
        self,
        profile: HouseholdProfile,
        evaluation: Optional[Dict[str, Any]],
        snapshot: Dict[str, Any],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        block = (evaluation or {}).get("income_and_buffer_stock_security") or {}
        expected_income = block.get("expected_real_disposable_income_million_vnd")
        base_income = profile.monthly_income_million_vnd
        if expected_income is not None and base_income:
            change = (float(expected_income) / float(base_income) - 1.0) * 100.0
            epsilon = self.channels.epsilon_for("income_employment")
            direction = (
                "negative"
                if change < -epsilon
                else "positive"
                if change > epsilon
                else "none"
            )
            return _channel(
                channel="income_employment",
                direction=direction,
                mechanism=(
                    "Cú sốc vĩnh viễn và tạm thời từ 5 nhân tố vĩ mô được truyền "
                    "qua hệ số BPP; mức bảo hiểm tiêu dùng một phần được áp dụng "
                    "theo hồ sơ hoặc tham số hiệu chuẩn."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Thu nhập thực khả dụng kỳ vọng thay đổi {change:+.3f}% "
                    f"({_fmt(float(expected_income))} triệu VND/tháng so với "
                    f"{_fmt(float(base_income))} triệu VND/tháng đầu vào)."
                ),
                source_or_formula=(
                    "M15-M19 (Blundell-Pistaferri-Preston 2008); "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        gdp = (snapshot.get("core_series") or {}).get("GDP_YOY")
        if gdp is not None:
            return _channel(
                channel="income_employment",
                direction="positive" if float(gdp) >= 0 else "negative",
                mechanism=(
                    "GDP YoY hiện tại là "
                    f"{_fmt(float(gdp))}% theo artifact v5.7; chưa có thu nhập "
                    "hàng tháng của hộ nên không quy đổi thành số tiền."
                ),
                magnitude_status="not_estimated",
            )
        return _channel(
            channel="income_employment",
            direction="ambiguous",
            mechanism=(
                "Chưa có số liệu thu nhập hộ gia đình hoặc artifact vĩ mô để "
                "ước lượng cú sốc thu nhập."
            ),
            magnitude_status="not_estimated",
        )

    def _borrowing(
        self,
        profile: HouseholdProfile,
        snapshot: Dict[str, Any],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        active = snapshot.get("active") or {}
        core = snapshot.get("core_series") or {}
        stressed_rate = active.get("interbank_peak_p95_pct")
        baseline_rate = core.get("IB_RATE")
        debt_service = profile.monthly_debt_service_million_vnd
        if (
            stressed_rate is not None
            and baseline_rate not in (None, 0)
            and debt_service is not None
        ):
            delta = float(stressed_rate) - float(baseline_rate)
            new_service = float(debt_service) * (1.0 + delta / 100.0)
            change = new_service - float(debt_service)
            epsilon = self.channels.epsilon_for("borrowing_cost")
            direction = (
                "negative"
                if change > epsilon
                else "positive"
                if change < -epsilon
                else "none"
            )
            return _channel(
                channel="borrowing_cost",
                direction=direction,
                mechanism=(
                    "Giả định khoản vay có lãi suất thả nổi và lãi suất cho vay "
                    "thay đổi cùng chiều với lãi suất liên ngân hàng trong kịch "
                    "bản căng thẳng."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Trả nợ hàng tháng thay đổi {change:+.3f} triệu VND "
                    f"({new_service:.3f} so với {float(debt_service):.3f} triệu VND)."
                ),
                source_or_formula=(
                    "D_stress = D_0 * (1 + (r_stress - r_0)/100); "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        return _channel(
            channel="borrowing_cost",
            direction="ambiguous",
            mechanism=(
                "Cần số tiền trả nợ hàng tháng và lãi suất cơ sở trong artifact "
                "để tính tác động lên nghĩa vụ nợ; hiện thiếu một trong hai."
            ),
            magnitude_status="not_estimated",
        )

    def _savings(
        self,
        profile: HouseholdProfile,
        snapshot: Dict[str, Any],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        active = snapshot.get("active") or {}
        core = snapshot.get("core_series") or {}
        liquid = profile.liquid_assets_million_vnd
        stressed_rate = active.get("interbank_peak_p95_pct")
        baseline_rate = core.get("IB_RATE")
        cpi = core.get("CPI_YOY")
        if (
            liquid is not None
            and stressed_rate is not None
            and baseline_rate is not None
            and cpi is not None
        ):
            baseline_real = float(baseline_rate) - float(cpi)
            stress_real = float(stressed_rate) - float(cpi)
            delta_monthly = (
                float(liquid) * (stress_real - baseline_real) / 100.0 / 12.0
            )
            epsilon = self.channels.epsilon_for("savings_return")
            direction = (
                "positive"
                if delta_monthly > epsilon
                else "negative"
                if delta_monthly < -epsilon
                else "none"
            )
            return _channel(
                channel="savings_return",
                direction=direction,
                mechanism=(
                    "Giả định tài sản lỏng hưởng lãi suất gần với lãi suất liên "
                    "ngân hàng; lợi suất thực bằng lãi suất danh nghĩa trừ CPI."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Thu nhập lãi thực hàng tháng thay đổi {delta_monthly:+.4f} "
                    f"triệu VND trên {_fmt(float(liquid))} triệu VND tài sản lỏng."
                ),
                source_or_formula=(
                    "r_real = r_nominal - CPI; monthly = liquid * Δr_real/100/12; "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        return _channel(
            channel="savings_return",
            direction="ambiguous",
            mechanism=(
                "Cần số dư tài sản lỏng và lãi suất/CPI trong artifact để tính "
                "lợi suất thực; hiện thiếu dữ liệu."
            ),
            magnitude_status="not_estimated",
        )

    def _fx(
        self,
        profile: HouseholdProfile,
        snapshot: Dict[str, Any],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        active = snapshot.get("active") or {}
        stress = active.get("fx_deprec_var_pct")
        assets = profile.fx_assets_million_vnd
        liabilities = profile.fx_liabilities_million_vnd
        if stress is not None and (assets is not None or liabilities is not None):
            net = float(assets or 0.0) - float(liabilities or 0.0)
            impact = net * float(stress) / 100.0
            epsilon = self.channels.epsilon_for("exchange_rate")
            direction = (
                "positive"
                if impact > epsilon
                else "negative"
                if impact < -epsilon
                else "none"
            )
            return _channel(
                channel="exchange_rate",
                direction=direction,
                mechanism=(
                    "Cú sốc mất giá nội tệ VaR 99% được áp lên vị thế ngoại tệ "
                    "ròng của hộ; nợ ngoại tệ làm tăng nghĩa vụ, tài sản ngoại "
                    "tệ làm tăng giá trị quy đổi."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Giá trị ròng vị thế ngoại tệ thay đổi {impact:+.3f} triệu VND "
                    f"khi tỷ giá stress {float(stress):+.3f}%."
                ),
                source_or_formula=(
                    "Impact = (FX_assets - FX_liabilities) * FX_shock/100; "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        return _channel(
            channel="exchange_rate",
            direction="ambiguous",
            mechanism=(
                "Chưa khai báo tài sản/nghĩa vụ ngoại tệ hoặc artifact không có "
                "cú sốc tỷ giá; không ước lượng số tiền."
            ),
            magnitude_status="not_estimated",
        )

    def _balance_sheet(
        self,
        profile: HouseholdProfile,
        evaluation: Optional[Dict[str, Any]],
        artifact_sha: str,
    ) -> MicroTransmissionChain:
        block = (evaluation or {}).get("income_and_buffer_stock_security") or {}
        buffer_months = block.get("actual_liquid_buffer_months")
        mdsr = block.get("modified_debt_service_ratio_mdsr")
        classification = block.get("hank_classification")
        mpc = block.get("expected_quarterly_mpc")
        if buffer_months is not None and mdsr is not None:
            direction = (
                "negative"
                if float(buffer_months) < float(self.params.liquid_buffer_threshold_months)
                or float(mdsr) > float(self.params.mdsr_threshold)
                else "positive"
            )
            return _channel(
                channel="assets_liabilities",
                direction=direction,
                mechanism=(
                    "Đệm thanh khoản, tỷ lệ trả nợ trên thu nhập thặng dư và "
                    "phân loại HANK hai tài sản xác định mức dễ bị tổn thương."
                ),
                magnitude_status="computed",
                magnitude_value=(
                    f"Đệm thanh khoản {float(buffer_months):.3f} tháng; MDSR "
                    f"{float(mdsr):.3f}; phân loại {classification}; MPC quý "
                    f"kỳ vọng {float(mpc):.3f}."
                ),
                source_or_formula=(
                    "M23-M24 (Kaplan-Moll-Violante 2018; Merton 1969); "
                    f"artifact:{artifact_sha or 'unavailable'}"
                ),
            )
        liquid = profile.liquid_assets_million_vnd
        expense = profile.monthly_expenditure_million_vnd
        if liquid is not None and expense:
            months = float(liquid) / float(expense)
            return _channel(
                channel="assets_liabilities",
                direction="negative"
                if months < float(self.params.liquid_buffer_threshold_months)
                else "positive",
                mechanism=(
                    "Chưa có artifact vĩ mô; chỉ tính đệm thanh khoản tĩnh từ "
                    "tài sản lỏng chia chi tiêu tháng. Ngưỡng tháng lấy từ "
                    "cấu hình micro_household.json, không phải số liệu thị trường."
                ),
                magnitude_status="computed",
                magnitude_value=f"Đệm thanh khoản tĩnh {months:.3f} tháng chi tiêu.",
                source_or_formula="liquid_assets / monthly_expenditure",
            )
        return _channel(
            channel="assets_liabilities",
            direction="ambiguous",
            mechanism=(
                "Chưa có đủ tài sản lỏng, nghĩa vụ nợ và chi tiêu để tính đệm "
                "thanh khoản hoặc MDSR."
            ),
            magnitude_status="not_estimated",
        )

    def _policy(
        self,
        snapshot: Dict[str, Any],
    ) -> MicroTransmissionChain:
        briefing = snapshot.get("briefing") or []
        direction = "ambiguous"
        mechanism = (
            "Chính sách tài khóa/an sinh có thể hỗ trợ hoặc làm chậm thu nhập "
            "hộ gia đình tùy đối tượng thụ hưởng; artifact v5.7 không cung cấp "
            "quy mô chuyển giao cho từng hộ."
        )
        if briefing:
            mechanism += " Tóm tắt engine: " + "; ".join(
                str(item) for item in briefing[:2]
            )
        return _channel(
            channel="public_transfers",
            direction=direction,
            mechanism=mechanism,
            magnitude_status="not_estimated",
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        run_id: str,
        raw: RawMaterial,
        household: Optional[HouseholdProfile],
        personal_context: Optional[PersonalContext],
        macro_bundle: Any = None,
    ) -> MicroBundle:
        profile = self._profile(household, personal_context)
        warnings: List[str] = []
        evaluation: Optional[Dict[str, Any]] = None
        if macro_bundle is None:
            warnings.append(
                "Micro chạy độc lập không có MacroResult; các kênh cần cú sốc "
                "vĩ mô được đánh dấu not_estimated."
            )
        else:
            try:
                evaluation = self._run_bridge(profile, macro_bundle)
            except MicroEngineError as exc:
                warnings.append(str(exc))
                evaluation = None

        snapshot = _macro_snapshot(macro_bundle)
        artifact_sha = str(
            ((snapshot.get("artifact_sha256") or ""))
            or ""
        )
        handlers = {
            "tornqvist_coli": lambda: self._prices(
                profile, evaluation, snapshot, artifact_sha
            ),
            "bpp_income": lambda: self._income(
                profile, evaluation, snapshot, artifact_sha
            ),
            "debt_service_rate": lambda: self._borrowing(
                profile, snapshot, artifact_sha
            ),
            "savings_real_return": lambda: self._savings(
                profile, snapshot, artifact_sha
            ),
            "fx_exposure": lambda: self._fx(
                profile, snapshot, artifact_sha
            ),
            "balance_sheet": lambda: self._balance_sheet(
                profile, evaluation, artifact_sha
            ),
            "policy_support": lambda: self._policy(snapshot),
        }
        chains: List[MicroTransmissionChain] = []
        for spec in self.channels.channels:
            if not spec.enabled:
                continue
            handler = handlers.get(spec.formula)
            if handler is None:
                warnings.append(
                    f"Kênh {spec.id} dùng formula chưa hỗ trợ: {spec.formula}"
                )
                continue
            chains.append(handler())

        assumptions = [
            "Chỉ các kênh có đủ số liệu người dùng và số liệu vĩ mô mới được "
            "tính số tiền; kênh thiếu dữ liệu được ghi not_estimated.",
            "Các tham số QUAIDS/BPP/EGM/HANK là cấu hình mô hình, không phải "
            "số liệu của hộ; xem config/micro_household.json.",
        ]
        if evaluation:
            assumptions.extend(
                str(item)
                for item in (evaluation.get("assumptions") or [])
                if str(item).strip()
            )
            warnings.extend(
                f"Thiếu đầu vào hộ: {item}"
                for item in (evaluation.get("missing_inputs") or [])
            )
        warnings.extend(getattr(macro_bundle, "warnings", []) or [])

        what_would_change = [
            "Thay đổi thu nhập, chi tiêu, tài sản lỏng hoặc nghĩa vụ nợ hàng tháng.",
            "Thay đổi cơ cấu chi tiêu 4 nhóm hàng (thực phẩm, nhà ở, năng lượng, dịch vụ).",
            "Thay đổi quy mô hộ (số người lớn, số trẻ em).",
            "Thay đổi kịch bản vĩ mô hoặc xác suất căng thẳng của engine v5.7.",
        ]
        result = MicroResult(
            run_id=run_id,
            upstream_macro_used=macro_bundle is not None,
            transmission_chains=chains,
            assumptions=assumptions,
            what_would_change_this=what_would_change,
            warnings=warnings,
            missing_inputs=(evaluation or {}).get("missing_inputs", []),
            channel_engine="micro_household_v5.8",
        )
        return MicroBundle(
            result=result,
            warnings=warnings,
            missing_inputs=(evaluation or {}).get("missing_inputs", []),
            evaluation=evaluation,
        )


__all__ = ["MicroBundle", "MicroChannelEngine"]
