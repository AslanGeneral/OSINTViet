# Phase 0 — Discovery Report

Date: 2026-10-29
Repository: `/home/lecuirassier/Documents/mirrored_backend` (branch `main`)
Scope: read-only discovery for `pipeline-v2-macro-micro-dual`.

## 1. Baseline (before any change)

Commands and results:

```
.venv/bin/python tests/test_macro_stress_v57.py
  [test] RFC 8785-compatible JCS + strict request contract: OK
  [test] v5.7 engine, gatekeeper, micro Arrow bridge, plugin: OK
  [test] live World Bank panel: SKIP (use --live)
  [test] ALL OK

.venv/bin/python tests/test_ingest_and_retrieval.py
  ... ALL_CHECKS_PASSED
```

No baseline failure. The working tree already contained uncommitted work from the
repository owner (v5.7 macro engine, web templates, workflow changes). That work
is treated as read-only unless a change is explicitly required for the new
pipeline.

## 2. Module map

| Area | Existing module(s) | Role |
|---|---|---|
| HTTP app | `app/main.py`, `app/api/v1/*` | FastAPI app, reading/indicating rooms, workflow API |
| CLI | `main.py`, `run_full_workflow.py`, `run_macro_stress.py` | Orchestrator CLI, live full-circle workflow, macro-only operator CLI |
| Config | `app/core/config.py` | Pydantic settings loaded from `.env` |
| Persistence | `app/core/database.py`, `app/models/article.py`, `app/models/workflow_run.py`, `init_db.py` | Async SQLite via SQLModel; `init_db()` creates missing tables and migrates the legacy `articles` table |
| Embedding | `app/services/embedding_engine.py` | Cohere Embed v5 Pro / local EmbeddingGemma 2, 256-d MRL, ChromaDB, CRAG/MMR, numerical conflict detection |
| LLM routing | `app/services/json_router.py`, `app/services/llm_engine.py`, `app/core/smart_key_router.py` | Local Ollama JSON-schema router, cloud key-pool wrapper, legacy structured LLM router |
| Live fetch | `app/scrapers/econ_article_fetcher.py` | Headless Chromium random economics article fetch |
| Article analysis | `app/services/full_circle_workflow.py` (`_analyze_and_filter`), `app/services/word_filter.py` | JSON-only article analysis with exact-quote verification and Vietnamese macro lexicon |
| Macro engine | `app/math_engine/macro_stress_v57/` | SPEC-MACRO-QUANT v5.7: calibration, RBPF, regimes, adversarial shock, Sobol simulation, gatekeeper, Wasserstein medoids, firm Arrow downscaling |
| Macro workflow adapter | `app/services/macro_stress.py` | World Bank 36-series panel -> `MacroStressRequest` -> `MacroStressArtifact`; disk cache outside git |
| Orchestrator | `app/orchestrator/universal_pipeline.py`, `app/orchestrator/tournament.py`, `app/orchestrator/cegar_loop.py` | 7-stage universal neuro-symbolic orchestrator (workers, critics, Elo, sandbox, Pareto export) |
| Verifier / memory | `app/memory/negative_memory_compiler.py`, `app/logic_engine/problog_scanner.py`, `verifier_compiled_memory.json` | Negative-rule compiler and ProbLog scanner; the JSON file is a shared negative-memory store, not an output verifier |
| Full-circle workflow | `app/services/full_circle_workflow.py` | Live fetch -> article analysis -> word filter -> v5.7 -> orchestrator -> deterministic recheck |
| UI | `app/templates/`, `app/static/` | Jinja2 pages: reading room, indicating room, co-scientist lab, admin |
| Tests | `tests/test_ingest_and_retrieval.py`, `tests/test_macro_stress_v57.py` | Standalone/pytest-compatible verification scripts |

## 3. `verifier_compiled_memory.json`

- Contents: `{"macro_econ": []}`.
- Written by `app/memory/negative_memory_compiler.py`
  (`NegativeMemoryCompiler.compile_to_long_term`) and read by
  `app/logic_engine/problog_scanner.py`.
- It stores long-term negative rules for the orchestrator. It does **not**
  validate engine output and does **not** contain embeddings.
- Decision: do not duplicate or replace it. The new re-validation stage is
  implemented with the existing embedding engine. Optionally, failed
  re-validation checks can be appended as lessons through the existing
  `NegativeMemoryCompiler` (disabled by default; see `config/pipeline_v2.json`).

## 4. User API key and personal context today

- The only LLM credentials are server-side `GEMINI_API_KEYS`, `EXTRA_API_KEYS`,
  `keys_pool.json`, `COHERE_API_KEY`, and `CLOUD_API_KEY`. There is no BYOK
  per-request path today.
- `app/services/llm_engine.py` and `app/services/json_router.py` never log the
  key, but the key is process-wide configuration.
- Personal context does not exist in the current workflow. The public article
  analysis and macro requests contain only article/analysis/panel data.
- Decision: add an in-memory BYOK router. It accepts a key from an environment
  variable name or stdin, never from a persisted file, never writes it to the
  cache log or artifacts, and redacts it from errors.

## 5. Input-fetch and output code paths

- Current live input: `HeadlessEconArticleFetcher.fetch_random()`.
- Current article analysis: `FullCircleWorkflow._analyze_and_filter()` with
  `LocalOllamaJsonRouter.generate_structured(..., response_model=ArticleAnalysis)`.
- Current macro stage: `build_macro_stress_request()` then
  `execute_macro_stress()` in `app/services/macro_stress.py`.
- Current orchestrator stage: `UniversalCoScientistPipeline.run()`.
- Current output: `WorkflowEnvelope` persisted to SQLite `workflow_runs` and
  `/tmp/osint_workflow/<run_id>/workflow.json`; CLI summary is printed by
  `run_full_workflow.py`.
- Decision: S1 wraps the existing fetcher and analysis method rather than
  re-implementing them. S6 reuses `UniversalCoScientistPipeline`; it does not
  re-implement the orchestrator. S7 adds the new `PipelineOutput` contract.

## 6. Reuse decisions per stage

| Stage | Decision | Reason |
|---|---|---|
| S1 Input / LLM fetch | Wrap `HeadlessEconArticleFetcher` and `FullCircleWorkflow._analyze_and_filter`; add optional in-memory BYOK OpenAI-compatible router and a query-breakdown structured call | Reuses the proven fetch/quote-verification path; adds only credential isolation and query breakdown |
| S2 Classifier | Lightweight deterministic lexicon + existing embedding model prototypes; optional LLM assist disabled by default | The embedding engine is already loaded and cheap for short texts; avoids an extra LLM call per request and keeps the classifier auditable. Threshold comes from a calibration file, not a constant. |
| S3 Cache Log | New SQLModel tables on the existing SQLite engine: `cache_log_entries`, `cache_purge_audits` | The existing persistence layer supports run-scoped rows; no new datastore is needed |
| S4a Macro | Thin adapter only: build request, execute existing `MacroStressEngineV57`, map signed artifact to `MacroResult` | Macro internals are untouched |
| S4b Micro | New isolated `app/math_engine/micro_household/` package (QUAIDS/COLI, BPP, EGM/HANK/VEP, Merton, FGT) + `app/pipeline_v2/micro_engine.py` channel evaluator | The second specification requires a separate lazy-loaded subsystem; it is not part of the macro core |
| S4c Mode router | New module with explicit `macro`, `micro`, `dual`, `auto` semantics | Keeps user-selected mode authoritative; dual is sequential |
| S5 Re-validation | New module using the existing `embedding_engine`; lexical fallback when embeddings are unavailable; purge in one SQLite transaction | Reuses the existing embedding component; no duplicate verifier |
| S6 Orchestrator | Call existing `UniversalCoScientistPipeline` with run-scoped inputs; disable raw ingestors during the call as the current workflow does | Reuses the existing orchestrator; avoids writing run data into the shared corpus |
| S7 Output | New `PipelineOutput` contract; no raw cache content or personal context unless explicitly requested | Matches the required output contract and privacy rules |

## 7. Conflicts / gaps found

1. **`verifier_compiled_memory.json` is not a verifier.** It is a negative-rule
   store. The new S5 is genuinely new; the existing memory is reused only as an
   optional lesson sink.
2. **`optimal_policy_u` and `pi_stress_regime2` are not in the signed v5.7
   artifact.** The micro bridge specification expects them. The implemented
   adapter derives `pi_stress_regime2` from
   `artifact.nowcast.regime.stress_probability_last` and uses a zero policy
   vector with an explicit warning when the artifact does not expose the
   optimal policy. No macro internals are changed.
3. **`PersonalContext` is smaller than the household profile required by the
   micro subsystem.** The public contract is kept exact. A separate optional
   `HouseholdProfile` file is added for the CLI/API; explicit numbers in
   `PersonalContext.free_text` are parsed only when present. Missing inputs make
   the affected channel `not_estimated`, never a fabricated number.
4. **The in-site user guide section described by the owner was not found in
   `app/templates/`.** The Reading Room has an LLM breakdown widget, but no BYOK
   or personal-context guidance. A new Vietnamese guide page is added without
   changing the existing Reading Room behavior.
5. **The macro artifact does not contain a baseline interest rate.** The
   borrowing-cost and savings-return channels use the macro artifact's
   Nelson-Siegel yield-curve P50/P95 spread as a stress measure. If the user
   has not supplied debt/liquid-asset amounts, the channel is `not_estimated`.

## 8. Open questions for the owner

1. Should `--mode auto` be allowed to choose `dual` without `--allow-dual`?
   Current implementation follows the prompt strictly: it refuses and emits
   `MODE_SELECTION_REQUIRED` unless `--allow-dual` is present.
2. Should failed re-validation write a lesson to the shared
   `verifier_compiled_memory.json`? Current default is `false` to avoid
   cross-run contamination; the hook exists.
3. The QUAIDS/LES/BPP parameter values supplied in the micro specification are
   illustrative calibration placeholders. They are loaded from
   `config/micro_household.json` and must be re-calibrated with VHLSS/VES data
   before production use.

## 9. Planned file layout (new only)

```
app/pipeline_v2/
  __init__.py
  contracts.py
  errors.py
  config.py
  engine_registry.py
  input_fetch.py
  byok_router.py
  classifier.py
  cache_log.py
  macro_adapter.py
  micro_engine.py
  mode_router.py
  revalidation.py
  orchestrator.py
  cli.py
app/math_engine/micro_household/
  __init__.py
  contracts.py
  coli_quaids_equivalence.py
  bpp_income_insurance.py
  buffer_stock_egm_hank_vep.py
  bridge_eco_engine.py
app/models/cache_log.py
app/api/v1/pipeline_v2.py
config/
  pipeline_v2.json
  classifier_calibration.json
  classifier_samples.jsonl
  micro_channels.json
  micro_household.json
scripts/
  calibrate_pipeline_v2.py
  stress_pipeline_v2.py
tests/
  test_pipeline_v2.py
  test_micro_household_subsystem.py
docs/
  HUONG_DAN_PIPELINE_V2.md
```

Existing files to be edited minimally: `app/core/database.py` (register new
tables), `app/core/config.py` (new path settings only), `app/main.py` (new route
and CLI dispatch), `run_full_workflow.py` (new mode dispatch), `.env.example`
(new config names only), `README.md` (new pipeline section), and a new Jinja2
guide template.
