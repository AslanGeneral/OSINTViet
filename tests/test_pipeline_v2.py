#!/usr/bin/env python3
"""Deterministic tests for pipeline v2 (S1-S7).

Run:
    .venv/bin/python tests/test_pipeline_v2.py

The suite never calls a live LLM, a live article endpoint or the real SQLite
database.  It uses the real classifier, cache-log lifecycle, re-validation and
micro engine, with a failing embedding client so lexical fallbacks are
exercised deterministically.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.pipeline_v2.cache_log import CacheLogStore
from app.pipeline_v2.classifier import InputClassifier
from app.pipeline_v2.cli import main as cli_main
from app.pipeline_v2.config import load_calibration, load_pipeline_config
from app.pipeline_v2.contracts import (
    ClassifierResult,
    HouseholdProfile,
    MacroClaim,
    MacroResult,
    MicroResult,
    MicroTransmissionChain,
    PersonalContext,
    RawInput,
    RawMaterial,
    StructuredError,
    new_run_id,
)
from app.pipeline_v2.errors import (
    CacheLogError,
    ModeSelectionRequired,
    PipelineError,
    redact_secrets,
)
from app.pipeline_v2.macro_adapter import _artifact_to_claims
from app.pipeline_v2.mode_router import resolve_mode
from app.pipeline_v2.orchestrator import (
    PipelineOrchestrator,
    _artifact_safe_raw_material,
    _artifact_safe_trace,
)
from app.pipeline_v2.revalidation import Revalidator


class FailingEmbeddingClient:
    """Force the documented lexical fallback without touching Ollama."""

    async def get_embeddings_batch(self, texts, input_type=None):
        raise RuntimeError("embedding disabled in deterministic tests")


def _classifier() -> InputClassifier:
    config = load_pipeline_config()
    return InputClassifier(
        config=config.classifier,
        calibration=load_calibration(config=config),
        embedding_client=FailingEmbeddingClient(),
    )


def _low_threshold_config(*, orchestrator_enabled: bool = False):
    config = load_pipeline_config().model_copy(deep=True)
    config.orchestrator.enabled = orchestrator_enabled
    config.input_fetch.require_live_article = False
    config.revalidation.lexical_overlap_threshold = 0.0
    config.revalidation.consistency_similarity_threshold = 0.0
    config.revalidation.min_claim_support_ratio = 0.0
    return config


def _raw_material(run_id: str, query: str) -> RawMaterial:
    return RawMaterial(
        run_id=run_id,
        query=query,
        requested_mode="macro",
        article_title=query[:80],
        article_source="test",
        article_content="",
        query_breakdown={
            "run_id": run_id,
            "query": query,
            "macro_topics": ["inflation"],
            "micro_topics": [],
            "search_queries": [query],
        },
        source_refs=[],
        warnings=[],
    )


class ContractAndClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_returns_valid_contracts(self) -> None:
        classifier = _classifier()
        macro = await classifier.classify(
            RawInput(
                run_id=new_run_id(),
                query="Lạm phát CPI và chính sách tiền tệ ảnh hưởng GDP thế nào?",
            )
        )
        self.assertEqual(macro.label, "macro")
        self.assertGreaterEqual(macro.confidence, 0.51)
        self.assertEqual(macro.routing_recommendation, "macro")

        micro = await classifier.classify(
            RawInput(
                run_id=new_run_id(),
                query=(
                    "Thu nhập, chi tiêu, tiết kiệm và khoản vay của hộ gia đình "
                    "tôi thay đổi ra sao?"
                ),
            )
        )
        self.assertEqual(micro.label, "micro")
        self.assertGreaterEqual(micro.confidence, 0.51)

        mixed = await classifier.classify(
            RawInput(
                run_id=new_run_id(),
                query=(
                    "Lạm phát, GDP, lãi suất, tỷ giá và chính sách tiền tệ tác "
                    "động đến thu nhập, chi tiêu, tiết kiệm và khoản vay hộ gia đình."
                ),
            )
        )
        self.assertEqual(mixed.label, "mixed")
        self.assertEqual(mixed.routing_recommendation, "dual")

    async def test_mode_router_explicit_and_auto(self) -> None:
        classifier = _classifier()
        config = load_pipeline_config()
        calibration = load_calibration(config=config)

        macro = await classifier.classify(
            RawInput(
                run_id=new_run_id(),
                query="Lạm phát CPI và chính sách tiền tệ tác động GDP thế nào?",
            )
        )
        decision = resolve_mode("macro", macro, calibration)
        self.assertEqual(decision.mode, "macro")
        self.assertEqual(decision.source, "explicit")

        low = ClassifierResult(
            run_id=new_run_id(),
            label="macro",
            confidence=0.01,
            rationale="low confidence test",
            routing_recommendation="macro",
        )
        with self.assertRaises(ModeSelectionRequired):
            resolve_mode("auto", low, calibration)
        decision = resolve_mode("auto", low, calibration, allow_dual=True)
        self.assertEqual(decision.mode, "dual")
        self.assertTrue(decision.warnings)

    def test_structured_errors_and_redaction(self) -> None:
        probe = "synthetic-redaction-probe"
        self.assertNotIn(probe, redact_secrets(f"api_key={probe}"))
        error = PipelineError(f"token={probe}").to_contract()
        self.assertIsInstance(error, StructuredError)
        self.assertNotIn(probe, error.message)


class CacheLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_lifecycle_opt_in_and_purge(self) -> None:
        store = CacheLogStore(persist=False)
        run_id = new_run_id()
        context = PersonalContext(
            free_text="Thu nhập 20 triệu, chi tiêu 15 triệu",
            persist_opt_in=False,
        )
        first = await store.write_entry(
            run_id=run_id,
            content="Truy vấn: lạm phát và chi phí sinh hoạt",
            personal_context=context,
            persist_personal_context=False,
        )
        second = await store.write_entry(
            run_id=run_id,
            content="Phân rã: giá thực phẩm, năng lượng",
            personal_context=context,
            persist_personal_context=False,
        )
        self.assertEqual(len(await store.get_entries(run_id)), 2)
        memory = store._memory[run_id]
        self.assertIsNone(memory[first.entry_id]["personal_context"])
        self.assertIsNone(memory[second.entry_id]["personal_context"])

        opted = await store.write_entry(
            run_id=run_id,
            content="Ngữ cảnh được phép lưu",
            personal_context=context,
            persist_personal_context=True,
        )
        self.assertIn(
            "Thu nhập 20 triệu",
            store._memory[run_id][opted.entry_id]["personal_context"],
        )

        with self.assertRaises(CacheLogError):
            await store.transition_run(run_id, "written", "verified")

        self.assertEqual(await store.mark_consumed(run_id), 3)
        self.assertEqual(await store.mark_revalidating(run_id), 3)
        self.assertEqual(await store.mark_verified(run_id), 3)

        dry = await store.purge_run(run_id, dry_run=True)
        self.assertEqual(dry.outcome, "dry_run")
        self.assertEqual(dry.entries_removed, 0)
        self.assertEqual(len(await store.get_entries(run_id)), 3)

        committed = await store.purge_run(run_id)
        self.assertTrue(committed.committed)
        self.assertEqual(committed.entries_removed, 3)
        self.assertEqual(await store.get_entries(run_id), [])

        again = await store.purge_run(run_id)
        self.assertEqual(again.outcome, "idempotent_noop")
        audit = await store.get_audit(again.audit_id)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["entries_removed"], 0)
        self.assertNotIn("content", audit)

    async def test_quarantine_never_purged_automatically(self) -> None:
        store = CacheLogStore(persist=False)
        run_id = new_run_id()
        entry = await store.write_entry(run_id=run_id, content="bad output")
        self.assertEqual(await store.mark_quarantined(run_id), 1)
        report = await store.purge_run(run_id)
        self.assertEqual(report.entries_removed, 0)
        self.assertEqual(report.outcome, "idempotent_noop")
        self.assertEqual(len(await store.list_quarantined(run_id)), 1)

        deleted = await store.delete_quarantined(run_id=run_id)
        self.assertEqual(deleted.entries_removed, 1)
        self.assertEqual(await store.list_quarantined(run_id), [])
        self.assertIsNotNone(await store.get_audit(deleted.audit_id))
        self.assertIsNotNone(entry.entry_id)

    async def test_secret_redacted_before_cache_write(self) -> None:
        store = CacheLogStore(persist=False)
        run_id = new_run_id()
        probe = "synthetic-redaction-probe"
        entry = await store.write_entry(
            run_id=run_id, content=f"header api_key={probe}"
        )
        self.assertNotIn(probe, entry.content)
        self.assertIn("[REDACTED]", entry.content)

    async def test_persistent_sqlite_lifecycle_and_context_column(self) -> None:
        import app.pipeline_v2.cache_log as cache_module
        from app.models.cache_log import CacheLogEntryRow

        with tempfile.TemporaryDirectory(prefix="pipeline_v2_sqlite_") as tmp:
            engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}/test.db")
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async def fake_init_db() -> None:
                async with engine.begin() as connection:
                    await connection.run_sync(SQLModel.metadata.create_all)

            original_init = cache_module.init_db
            original_factory = cache_module.async_session_factory
            cache_module.init_db = fake_init_db
            cache_module.async_session_factory = factory
            try:
                store = CacheLogStore(persist=True)
                run_id = new_run_id()
                context = PersonalContext(
                    free_text="Thu nhập 20 triệu",
                    persist_opt_in=False,
                )
                entry = await store.write_entry(
                    run_id=run_id,
                    content="nội dung chạy thử",
                    personal_context=context,
                    persist_personal_context=False,
                )
                async with factory() as session:
                    row = await session.get(CacheLogEntryRow, entry.entry_id)
                    self.assertIsNotNone(row)
                    self.assertIsNone(row.personal_context_json)

                await store.mark_consumed(run_id)
                await store.mark_revalidating(run_id)
                await store.mark_verified(run_id)
                report = await store.purge_run(run_id)
                self.assertEqual(report.entries_removed, 1)
                self.assertEqual(len(await store.get_entries(run_id)), 0)
                audit = await store.get_audit(report.audit_id)
                self.assertEqual(audit["entries_removed"], 1)
                self.assertNotIn("content", audit)
            finally:
                cache_module.init_db = original_init
                cache_module.async_session_factory = original_factory
                await engine.dispose()


def _entry(run_id: str, content: str):
    return SimpleNamespace(entry_id=f"cache_{content[:8]}_{run_id[-4:]}", content=content)


class RevalidationTests(unittest.IsolatedAsyncioTestCase):
    async def _store_with_entries(self, contents):
        store = CacheLogStore(persist=False)
        run_id = new_run_id()
        entries = [
            await store.write_entry(run_id=run_id, content=content)
            for content in contents
        ]
        await store.mark_consumed(run_id)
        return store, run_id, entries

    async def test_pass_then_purge(self) -> None:
        text = "Lạm phát làm chi phí sinh hoạt của hộ gia đình tăng."
        store, run_id, entries = await self._store_with_entries([text, text])
        macro = MacroResult(
            run_id=run_id,
            summary=text,
            claims=[
                MacroClaim(
                    text=text,
                    support=[entries[0].entry_id, "artifact:sha256:test"],
                )
            ],
        )
        micro = MicroResult(
            run_id=run_id,
            upstream_macro_used=True,
            transmission_chains=[
                MicroTransmissionChain(
                    channel="prices",
                    direction="negative",
                    mechanism=text,
                    magnitude_status="computed",
                    magnitude_value="+3.000%",
                    source_or_formula="artifact:sha256:test",
                )
            ],
            assumptions=["giả định kiểm thử"],
        )
        outcome = await Revalidator(embedding_client=FailingEmbeddingClient()).run(
            run_id=run_id,
            mode="dual",
            macro=macro,
            micro=micro,
            entries=entries,
            store=store,
        )
        self.assertTrue(outcome.report.passed)
        self.assertTrue(outcome.purge.committed)
        self.assertEqual(outcome.purge.entries_removed, 2)
        self.assertEqual(await store.get_entries(run_id), [])

    async def test_failure_quarantines_without_purge(self) -> None:
        text = "Lạm phát làm chi phí sinh hoạt của hộ gia đình tăng."
        store, run_id, entries = await self._store_with_entries([text])
        macro = MacroResult(
            run_id=run_id,
            summary=text,
            claims=[MacroClaim(text=text, support=[])],
        )
        outcome = await Revalidator(embedding_client=FailingEmbeddingClient()).run(
            run_id=run_id,
            mode="macro",
            macro=macro,
            micro=None,
            entries=entries,
            store=store,
        )
        self.assertFalse(outcome.report.passed)
        self.assertEqual(outcome.purge.outcome, "not_purged_quarantined")
        remaining = await store.get_entries(run_id)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].state, "quarantined")

    async def test_dry_run_keeps_verified_rows(self) -> None:
        text = "Chi phí sinh hoạt tăng do giá thực phẩm."
        store, run_id, entries = await self._store_with_entries([text])
        macro = MacroResult(
            run_id=run_id,
            summary=text,
            claims=[
                MacroClaim(
                    text=text,
                    support=[entries[0].entry_id, "artifact:sha256:test"],
                )
            ],
        )
        outcome = await Revalidator(embedding_client=FailingEmbeddingClient()).run(
            run_id=run_id,
            mode="macro",
            macro=macro,
            micro=None,
            entries=entries,
            store=store,
            dry_run=True,
        )
        self.assertTrue(outcome.report.passed)
        self.assertTrue(outcome.purge.dry_run)
        self.assertEqual(outcome.purge.entries_removed, 0)
        remaining = await store.get_entries(run_id)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].state, "verified")

    async def test_empty_cache_fails_closed(self) -> None:
        store = CacheLogStore(persist=False)
        run_id = new_run_id()
        outcome = await Revalidator(embedding_client=FailingEmbeddingClient()).run(
            run_id=run_id,
            mode="macro",
            macro=None,
            micro=None,
            entries=[],
            store=store,
        )
        self.assertFalse(outcome.report.passed)
        self.assertEqual(outcome.purge.outcome, "not_purged_quarantined")

    async def test_dual_mode_integrity_requires_computed_artifact_channel(
        self,
    ) -> None:
        run_id = new_run_id()
        macro = MacroResult(
            run_id=run_id,
            summary="Bối cảnh vĩ mô.",
            claims=[MacroClaim(text="Bối cảnh vĩ mô.")],
        )
        no_channel = MicroResult(
            run_id=run_id,
            upstream_macro_used=True,
            transmission_chains=[
                MicroTransmissionChain(
                    channel="assets_liabilities",
                    direction="ambiguous",
                    mechanism="Thiếu dữ liệu.",
                    magnitude_status="not_estimated",
                )
            ],
            assumptions=["giả định kiểm thử"],
        )
        revalidator = Revalidator(embedding_client=FailingEmbeddingClient())
        failed = revalidator._mode_integrity("dual", macro, no_channel)
        self.assertFalse(failed.passed)
        self.assertTrue(
            any("computed" in item for item in failed.failed_items)
        )

        computed_no_artifact = no_channel.model_copy(
            update={
                "transmission_chains": [
                    MicroTransmissionChain(
                        channel="assets_liabilities",
                        direction="negative",
                        mechanism="Đệm thanh khoản thấp.",
                        magnitude_status="computed",
                        magnitude_value="2.0 tháng",
                        source_or_formula="liquid_assets / monthly_expenditure",
                    )
                ]
            }
        )
        failed = revalidator._mode_integrity("dual", macro, computed_no_artifact)
        self.assertFalse(failed.passed)
        self.assertTrue(
            any("artifact" in item for item in failed.failed_items)
        )

        computed_with_artifact = no_channel.model_copy(
            update={
                "transmission_chains": [
                    MicroTransmissionChain(
                        channel="prices",
                        direction="negative",
                        mechanism="Giá tăng.",
                        magnitude_status="computed",
                        magnitude_value="+3.0%",
                        source_or_formula="artifact:sha256:test",
                    )
                ]
            }
        )
        passed = revalidator._mode_integrity("dual", macro, computed_with_artifact)
        self.assertTrue(passed.passed)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="pipeline_v2_e2e_")
        self.config = _low_threshold_config()
        self.config.artifact_dir = self.tempdir.name
        self.stores = []

        async def fake_fetch(raw_input, context, config, *, router=None, use_live=None):
            return _raw_material(raw_input.run_id, raw_input.query)

        def fake_store_factory(config, *, persist=True):
            store = CacheLogStore(config, persist=False)
            self.stores.append(store)
            return store

        self.patches = [
            patch("app.pipeline_v2.orchestrator.fetch_material", fake_fetch),
            patch("app.pipeline_v2.orchestrator.CacheLogStore", fake_store_factory),
        ]
        for patcher in self.patches:
            patcher.start()

    async def asyncTearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tempdir.cleanup()

    def _orchestrator(self) -> PipelineOrchestrator:
        return PipelineOrchestrator(
            config=self.config, embedding_client=FailingEmbeddingClient()
        )

    async def test_macro_mode_s1_to_s7(self) -> None:
        query = "Lạm phát CPI và chính sách tiền tệ tác động GDP thế nào?"

        async def fake_macro(*, run_id, raw, cache_entries, config, artifact_root, write_artifact):
            from app.pipeline_v2.macro_adapter import MacroBundle

            support = [entry.entry_id for entry in cache_entries]
            result = MacroResult(
                run_id=run_id,
                summary=raw.query,
                claims=[
                    MacroClaim(
                        text=raw.query,
                        support=[*support, "artifact:sha256:test"],
                    )
                ],
                warnings=[],
            )
            payload = {
                "F_medoids_terminal": np.zeros((50, 5)).tolist(),
                "cluster_weights_q": (np.ones(50) / 50).tolist(),
                "optimal_policy_u": [0.0, 0.0, 0.0],
                "pi_stress_regime2": 0.2,
                "macro_snapshot": {"artifact_sha256": "test"},
            }
            return MacroBundle(
                result=result,
                artifact=SimpleNamespace(artifact_sha256="test"),
                artifact_path="",
                micro_payload=payload,
                warnings=[],
            )

        with patch("app.pipeline_v2.orchestrator.run_macro", fake_macro):
            result = await self._orchestrator().run(
                query=query,
                requested_mode="macro",
                dry_run=False,
                enable_orchestrator=False,
            )
        output = result.output
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(output.mode_used, "macro")
        self.assertIn(output.status, {"ok", "ok_with_warnings"})
        self.assertIsNotNone(output.macro)
        self.assertIsNone(output.micro)
        self.assertTrue(output.validation.passed)
        self.assertEqual(output.validation.purge.outcome, "committed")
        self.assertEqual(self.stores[0].persist, False)
        self.assertEqual(await self.stores[0].get_entries(output.run_id), [])

    async def test_micro_mode_uses_household_profile(self) -> None:
        profile = HouseholdProfile(
            monthly_income_million_vnd=22.0,
            monthly_expenditure_million_vnd=18.0,
            budget_shares_w0=[0.45, 0.25, 0.12, 0.18],
            n_adults=2,
            n_children=1,
            liquid_assets_million_vnd=18.0,
            illiquid_assets_million_vnd=450.0,
            monthly_debt_service_million_vnd=3.2,
        )
        result = await self._orchestrator().run(
            query="Thu nhập và chi tiêu hộ gia đình tôi thay đổi ra sao?",
            requested_mode="micro",
            household_profile=profile,
            dry_run=False,
            enable_orchestrator=False,
        )
        output = result.output
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(output.mode_used, "micro")
        self.assertIsNone(output.macro)
        self.assertIsNotNone(output.micro)
        self.assertFalse(output.micro.upstream_macro_used)
        self.assertTrue(output.validation.passed)
        self.assertTrue(output.micro.transmission_chains)

    async def test_dual_mode_feeds_macro_into_micro(self) -> None:
        query = "Lạm phát vĩ mô tác động thu nhập và chi tiêu hộ gia đình thế nào?"

        async def fake_macro(*, run_id, raw, cache_entries, config, artifact_root, write_artifact):
            from app.pipeline_v2.macro_adapter import MacroBundle

            support = [entry.entry_id for entry in cache_entries]
            rng = np.random.default_rng(3)
            payload = {
                "F_medoids_terminal": rng.normal(0, 0.2, (50, 5)).tolist(),
                "cluster_weights_q": (np.ones(50) / 50).tolist(),
                "optimal_policy_u": [0.0, 0.0, 0.0],
                "pi_stress_regime2": 0.35,
                "macro_snapshot": {"artifact_sha256": "test"},
            }
            result = MacroResult(
                run_id=run_id,
                summary=raw.query,
                claims=[
                    MacroClaim(
                        text=raw.query,
                        support=[*support, "artifact:sha256:test"],
                    )
                ],
            )
            return MacroBundle(
                result=result,
                artifact=SimpleNamespace(artifact_sha256="test"),
                artifact_path="",
                micro_payload=payload,
                warnings=[],
            )

        profile = HouseholdProfile(
            monthly_income_million_vnd=22.0,
            monthly_expenditure_million_vnd=18.0,
            budget_shares_w0=[0.45, 0.25, 0.12, 0.18],
            n_adults=2,
            n_children=1,
            liquid_assets_million_vnd=18.0,
            illiquid_assets_million_vnd=450.0,
            monthly_debt_service_million_vnd=3.2,
        )
        with patch("app.pipeline_v2.orchestrator.run_macro", fake_macro):
            result = await self._orchestrator().run(
                query=query,
                requested_mode="dual",
                household_profile=profile,
                dry_run=False,
                enable_orchestrator=False,
            )
        output = result.output
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(output.mode_used, "dual")
        self.assertIsNotNone(output.macro)
        self.assertIsNotNone(output.micro)
        self.assertTrue(output.micro.upstream_macro_used)
        computed = [
            chain
            for chain in output.micro.transmission_chains
            if chain.magnitude_status == "computed"
        ]
        self.assertTrue(computed)
        self.assertTrue(
            any("artifact:" in (chain.source_or_formula or "") for chain in computed)
        )
        self.assertTrue(output.validation.passed)

    async def test_dry_run_skips_artifacts_and_orchestrator(self) -> None:
        self.config.orchestrator.enabled = True
        result = await self._orchestrator().run(
            query="Thu nhập và chi tiêu hộ gia đình tôi thay đổi ra sao?",
            requested_mode="micro",
            household_profile=HouseholdProfile(
                monthly_income_million_vnd=20.0,
                monthly_expenditure_million_vnd=15.0,
                budget_shares_w0=[0.45, 0.25, 0.12, 0.18],
                n_adults=2,
                n_children=0,
            ),
            dry_run=True,
        )
        output = result.output
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(
            all(value == "" for value in output.artifacts.values())
        )
        self.assertTrue(output.validation.purge.dry_run)
        self.assertTrue(
            any("--dry-run bỏ qua S6" in warning for warning in output.warnings)
        )
        self.assertEqual(list(Path(self.tempdir.name).rglob("*.json")), [])

    async def test_auto_mode_without_signal_returns_structured_error(self) -> None:
        result = await self._orchestrator().run(
            query="Xin chào",
            requested_mode="auto",
            dry_run=True,
        )
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.output.status, "failed")
        self.assertEqual(result.output.error.code, "MODE_SELECTION_REQUIRED")
        self.assertEqual(
            result.output.mode_used,
            result.output.classifier.routing_recommendation,
        )

    async def test_s6_orchestrator_wrapper_returns_summary(self) -> None:
        class FakeOutput:
            task_id = "task_test"
            domain_tag = "macro_econ"
            viable_pareto_frontier = [1, 2]
            rejected_dead_branches = []
            newly_compiled_negative_rules = [{"rule": "x"}]

            def model_dump(self, mode="json"):
                return {"task_id": self.task_id, "domain_tag": self.domain_tag}

        class FakePipeline:
            def __init__(self, router, blackboard):
                self.router = router

            async def run(self, *args, **kwargs):
                return FakeOutput()

        router = SimpleNamespace(
            trace=SimpleNamespace(records=[{"role": "test"}]),
            last_model="fake",
        )
        bundle = SimpleNamespace(
            router=router, extra_secrets=[], warnings=[]
        )
        with patch(
            "app.pipeline_v2.orchestrator.RouterBundle", SimpleNamespace
        ), patch(
            "app.orchestrator.universal_pipeline.UniversalCoScientistPipeline",
            FakePipeline,
        ), patch("app.plugins.load_plugins", lambda: None):
            summary = await self._orchestrator()._run_orchestrator(
                run_id=new_run_id(),
                raw=_raw_material(new_run_id(), "Lạm phát CPI"),
                macro=None,
                micro=None,
                bundle=bundle,
                run_dir=Path(self.tempdir.name),
                write_artifacts=False,
                persist_opt_in=False,
                warnings=[],
            )
        self.assertIsNotNone(summary)
        self.assertEqual(summary["task_id"], "task_test")
        self.assertEqual(summary["viable_count"], 2)
        self.assertEqual(summary["artifact_path"], "")


class CliTests(unittest.TestCase):
    def test_usage_error_is_structured_json(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli_main(["--mode", "invalid", "--query", "x"])
        self.assertEqual(code, 2)
        payload = json.loads(stderr.getvalue())
        self.assertTrue(payload["error"])
        self.assertEqual(payload["code"], "INVALID_INPUT")
        self.assertIn("--mode", payload["message"])


class MacroAdapterLanguageTests(unittest.TestCase):
    def test_claims_use_vietnamese_templates_not_english_briefing(self) -> None:
        active = SimpleNamespace(
            gdp_yoy_mean_pct=0.8,
            gdp_yoy_trough_mean_pct=-1.4,
            gdp_yoy_std_pct=0.2,
            gdp_tail_loss_var_pct=2.6,
            gdp_tail_loss_es_pct=2.7,
            fx_deprec_var_pct=8.0,
            fx_reserves_drain_billion_usd=42.0,
            interbank_peak_p95_pct=5.1,
            npl_breach_probability=0.998,
        )
        passive = SimpleNamespace(
            gdp_yoy_mean_pct=2.8,
            gdp_yoy_trough_mean_pct=-1.9,
            gdp_tail_loss_var_pct=3.0,
            interbank_peak_p95_pct=6.9,
            npl_breach_probability=1.0,
        )
        artifact = SimpleNamespace(
            artifact_sha256="abc123",
            request_id="req_test",
            branches={"active": active, "passive": passive},
            nowcast={"regime": {"stress_probability_last": 0.112}},
            gatekeeper={
                "joint_valid_particles": 8192,
                "acceptance_rate": 1.0,
            },
            adversarial_shock={"best_objective": 2.0468},
            deterministic_briefing=[
                "The v5.7 adversarial layer selects a copula-GPD shock.",
                "The passive branch's weighted 99% GDP tail loss is 3.0 pp.",
            ],
        )
        texts = [
            claim.text for claim in _artifact_to_claims(artifact, ["cache_x"])
        ]
        self.assertFalse(any("The v5.7" in text for text in texts))
        self.assertFalse(any("passive branch" in text for text in texts))
        self.assertTrue(any("Lớp đối kháng v5.7" in text for text in texts))
        self.assertTrue(any("hành lang ba tầng của NHNN" in text for text in texts))


class CacheContentTests(unittest.TestCase):
    def test_opt_out_removes_personal_parameters_from_cache_payload(self) -> None:
        orchestrator = PipelineOrchestrator(config=_low_threshold_config())
        run_id = new_run_id()
        raw = _raw_material(run_id, "Thu nhập 20 triệu, chi tiêu 15 triệu")
        raw.query_breakdown.personal_dimensions = ["income", "expenditure"]
        raw.query_breakdown.explicit_parameters = {
            "monthly_income_million_vnd": 20.0,
            "monthly_expenditure_million_vnd": 15.0,
        }
        opt_in = orchestrator._cache_contents(raw, persist_opt_in=True)
        opt_out = orchestrator._cache_contents(raw, persist_opt_in=False)
        opt_in_text = "\n".join(item[0] for item in opt_in)
        opt_out_text = "\n".join(item[0] for item in opt_out)
        self.assertIn("monthly_income_million_vnd", opt_in_text)
        self.assertNotIn("monthly_income_million_vnd", opt_out_text)
        self.assertIn('"personal_dimensions": []', opt_out_text)

    def test_cache_content_has_no_api_key(self) -> None:
        orchestrator = PipelineOrchestrator(config=_low_threshold_config())
        raw = _raw_material(new_run_id(), "Lạm phát CPI")
        contents = orchestrator._cache_contents(raw, persist_opt_in=False)
        text = json.dumps([item[0] for item in contents], ensure_ascii=False)
        self.assertNotIn("sk-", text)

    def test_opt_out_raw_artifact_redacts_personal_parameters(self) -> None:
        run_id = new_run_id()
        raw = _raw_material(run_id, "Thu nhập 20 triệu, chi tiêu 15 triệu")
        raw.query_breakdown.personal_dimensions = ["income", "expenditure"]
        raw.query_breakdown.explicit_parameters = {
            "monthly_income_million_vnd": 20.0,
            "monthly_expenditure_million_vnd": 15.0,
        }
        opt_out = _artifact_safe_raw_material(raw, persist_opt_in=False)
        breakdown = opt_out["query_breakdown"]
        self.assertNotIn("personal_dimensions", breakdown)
        self.assertNotIn("explicit_parameters", breakdown)
        self.assertTrue(breakdown["personal_parameters_redacted"])
        self.assertNotIn("monthly_income_million_vnd", json.dumps(opt_out))

        opt_in = _artifact_safe_raw_material(raw, persist_opt_in=True)
        self.assertEqual(
            opt_in["query_breakdown"]["explicit_parameters"][
                "monthly_income_million_vnd"
            ],
            20.0,
        )

    def test_opt_out_llm_trace_has_no_prompt_or_response_content(self) -> None:
        trace = [
            {
                "role": "query_breakdown",
                "user_prompt_sha256": "abc",
                "user_prompt_excerpt": "Thu nhập 20 triệu",
                "parsed": {"explicit_parameters": {"income": 20.0}},
                "response_excerpt": "income",
                "validation_error": "bad",
                "transport_error": "timeout",
                "model": "fake",
            },
            {
                "role": "hypothesis",
                "user_prompt_sha256": "def",
                "user_prompt_excerpt": "context",
                "parsed": {"x": 1},
                "response_excerpt": "y",
                "model": "fake",
            },
        ]
        safe = _artifact_safe_trace(trace, persist_opt_in=False)
        text = json.dumps(safe, ensure_ascii=False)
        for forbidden in (
            "user_prompt_excerpt",
            "response_excerpt",
            '"parsed"',
            "validation_error",
            "transport_error",
            "Thu nhập 20 triệu",
        ):
            self.assertNotIn(forbidden, text)
        self.assertTrue(safe[0]["prompt_redacted"])
        self.assertNotIn("user_prompt_sha256", safe[0])
        self.assertEqual(safe[1]["user_prompt_sha256"], "def")

        opt_in = _artifact_safe_trace(trace, persist_opt_in=True)
        self.assertIn("user_prompt_excerpt", opt_in[0])


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for case in (
        ContractAndClassifierTests,
        CacheLogTests,
        RevalidationTests,
        OrchestratorTests,
        CliTests,
        MacroAdapterLanguageTests,
        CacheContentTests,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
