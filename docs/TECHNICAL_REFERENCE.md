# Technical Reference

**Project:** Mirrored Backend / CLIO-CUSP macro-financial and household stress system  
**Document version:** 1.0  
**Last updated:** 6 November 2026

This document describes the technologies, mathematical formulas, numerical
defaults, and economic theories used by the repository. It is written for
engineers, reviewers, and researchers who need to verify what the code actually
does rather than what a prose specification says it should do.

The source of truth is the implementation:

- `app/pipeline_v2/` for orchestration, classification, caching, revalidation,
  and the micro channel layer.
- `app/math_engine/macro_stress_v57/` for the macro-financial engine.
- `app/math_engine/micro_household/` for the household subsystem.
- `config/pipeline_v2.json`, `config/classifier_calibration.json`,
  `config/micro_household.json`, and `config/micro_channels.json` for runtime
  parameters.
- `app/core/config.py` and `.env.example` for environment-level defaults.

Where the code and a written specification disagree, this document follows the
code. Several places are explicitly labelled as approximations or placeholders;
those labels are preserved here.

---

## Contents

1. [Scope and conventions](#1-scope-and-conventions)
2. [System architecture](#2-system-architecture)
3. [Public and internal contracts](#3-public-and-internal-contracts)
4. [Pipeline stages S1 to S7](#4-pipeline-stages-s1-to-s7)
5. [Macro-financial engine v5.7](#5-macro-financial-engine-v57)
6. [Household micro engine](#6-household-micro-engine)
7. [Economic theory map](#7-economic-theory-map)
8. [Privacy, security, and governance](#8-privacy-security-and-governance)
9. [Configuration reference](#9-configuration-reference)
10. [Operating the system](#10-operating-the-system)
11. [Appendices](#11-appendices)
12. [Glossary](#12-glossary)

---

## 1. Scope and conventions

### 1.1 What the system computes

The system answers two related classes of question:

1. **Macro-financial stress.** Given a 36-column annual panel of Vietnamese
   macro-financial indicators, the engine estimates a factor-augmented
   regime-switching state-space model, searches for plausible adverse shocks,
   propagates three counterfactual branches, filters implausible paths, and
   reports tail risk, scenario archetypes, and variance decompositions.
2. **Household impact.** Given explicit household inputs and, optionally, the
   macro artifact, the system projects sectoral prices, evaluates demand and
   cost-of-living effects, estimates income and balance-sheet exposure, and
   reports vulnerability. Missing household inputs are never filled with
   invented amounts; the affected channel is marked `not_estimated`.

A run can execute macro only, micro only, or both (`dual`). The classifier is
advisory; an explicit requested mode always wins.

### 1.2 Conventions

- **Money units.** Household amounts are in **million VND per month** unless a
  field name says otherwise. Macro reserves and balance-of-payments series are
  in **billion USD**; interest rates, inflation, growth, and ratios are in
  **percent**.
- **Time.** The macro panel is annual, 1996–2025. The simulation horizon is
  measured in years and defaults to 4. The household EGM is written in
  quarterly-recursion form, but the reported MPC is labelled
  `expected_quarterly_mpc`.
- **Weights.** Probability weights are normalized to sum to one before they are
  used in expectations, quantiles, or risk measures.
- **Logarithms.** `ln` is the natural logarithm. Price indices are normalized
  so that the pre-shock price vector is `p_0 = 1` for every good.
- **Clipping.** Clips are stated explicitly. A clip is part of the model, not a
  display convention.
- **Notation.** Vectors are columns unless a transpose is shown. `1{·}` is the
  indicator function. `Φ(·)` is the standard normal CDF. `E[·]` is an
  expectation under the stated weights.

### 1.3 Current status and known approximations

The repository is a working reference implementation, not a production
risk-management system. The most important qualifications are:

- The household coefficients are **illustrative calibration placeholders**
  supplied with the micro specification. `config/micro_household.json` states
  that they require recalibration with VHLSS/VES data before production use.
- Layer 0 of the macro engine is a deterministic PCA/VAR(5) plus hard-EM
  two-regime covariance approximation. The artifact records
  `estimation_backend = "pca_var5_hard_em_covariance"` so a later production EM
  fit can replace it without changing the JSON contract.
- The firm-level micro satellite is a deterministic **synthetic fixture** unless
  a real Arrow IPC firm table is supplied. The artifact records the mode.
- The optional `optimal_policy_u` is not exposed by the hashed macro artifact.
  The pipeline adapter passes a zero vector and records that choice as an
  assumption.
- The S6 universal orchestrator is an LLM-driven layer. Its output can vary
  between runs even when the deterministic stages are identical.

---

## 2. System architecture

### 2.1 Repository map

| Path | Responsibility |
|---|---|
| `app/pipeline_v2/` | Seven-stage pipeline, contracts, classifier, cache log, revalidation, mode routing, macro adapter, micro channel engine, CLI and HTTP surface |
| `app/math_engine/macro_stress_v57/` | Layer 0 to Layer 6 macro engine: calibration, RBPF/RTS, regimes, adversarial search, simulation, gatekeeper, GFEVD, satellites, micro downscaling |
| `app/math_engine/micro_household/` | Price projection, QUAIDS, index numbers, BPP income process, Carroll-Toche buffer stock, EGM, HANK classification, Merton share, FGT/VEP |
| `app/math_engine/clio_cusp/`, `app/matraix_sim/` | Separate CLIO-CUSP kernel and simulation code; not part of the pipeline v2 macro/micro path |
| `app/orchestrator/` | Universal Co-Scientist pipeline, CEGAR loop, tournament DAG |
| `app/logic_engine/` | Dynamic ProbLog scanner |
| `app/sandbox/` | Sandbox runner and HMAC attestation |
| `app/memory/` | Blackboard, negative-memory compiler, persistence |
| `app/services/` | World Bank panel adapter, embeddings, LLM router, live article workflow |
| `app/api/v1/pipeline_v2.py` | HTTP endpoint for pipeline v2 |
| `config/` | Runtime JSON configuration |
| `tests/` | Unit and integration tests |
| `scripts/` | Calibration, stress matrix, and CLIO-CUSP stress tools |

### 2.2 End-to-end data flow

```text
RawInput
  │
  ├─ S1  input fetch + deterministic query breakdown + optional LLM topics
  │
  ├─ S2  lexicon + embedding-prototype classifier (advisory)
  │
  ├─ S3  processed content and optional vectors written to cache_log_entries
  │
  ├─ S4  mode router
  │      ├─ macro adapter → World Bank 36-series panel → v5.7 engine
  │      ├─ micro channel engine → household bridge (macro optional)
  │      └─ dual: macro first, then micro with the macro payload
  │
  ├─ S5  grounding, consistency, completeness, mode integrity
  │      └─ all pass → verified → purge; any fail → quarantined
  │
  ├─ S6  UniversalCoScientistPipeline (LLM, ProbLog, sandbox, tournament)
  │
  └─ S7  PipelineOutput JSON (no raw cache content, no personal context)
```

The macro and micro engines are isolated. The macro bridge writes an artifact
and a read-only payload; the micro engine consumes the payload but never mutates
macro state. In `dual` mode the micro stage is skipped if the macro stage fails,
so a failed macro run cannot be silently replaced by a micro answer.

### 2.3 Technology stack

#### Core runtime

| Technology | Version or role |
|---|---|
| Python | 3.11 or newer |
| FastAPI | HTTP API |
| Uvicorn | ASGI server |
| Pydantic v2 | Strict request, artifact, and output contracts |
| SQLModel / SQLAlchemy | SQLite persistence for cache log and blackboard |
| aiosqlite | Asynchronous SQLite driver |
| httpx | World Bank, Cohere, and OpenAI-compatible HTTP clients |
| asyncio | Stage orchestration and concurrent fetches |

#### Numerical and data libraries

| Technology | Role |
|---|---|
| NumPy | Arrays, linear algebra, SVD, eigen decomposition, random generators |
| SciPy | Normal/t/chi-square distributions, Kendall tau, generalized Pareto fit, Sobol sampler, spatial distances, special functions |
| pandas / Polars | Tabular utilities in adjacent modules; the v5.7 engine uses NumPy arrays internally |
| PyArrow | Uncompressed Arrow IPC and zero-copy column reads |
| scikit-learn | Optional dependencies in adjacent modules; not required by the v5.7 core path |
| ChromaDB | Persistent vector store for retrieval |
| ProbLog | Probabilistic logic inference |
| Jinja2 | Prompt and report templates |
| python-dotenv | Environment configuration |

#### Models and external services

| Service | Use |
|---|---|
| Local Ollama | Default LLM and embedding provider (`llama3.2:3b` for generation in the reference environment; `embeddinggemma-2:740m` for embeddings) |
| Cohere Embed v5 Pro | Optional embedding provider when `COHERE_API_KEY` is set |
| OpenAI-compatible endpoint | Optional BYOK generation provider |
| World Bank Indicators API | 36-series annual macro panel |
| Headless Chromium | Optional live economics-article fetch |
| ChromaDB | Retrieval over ingested articles and evidence |
| MCP | Optional tool/resource integration in the orchestrator |

#### Engineering and verification

| Tool | Role |
|---|---|
| unittest / pytest-style tests | Deterministic unit and integration tests |
| `scripts/calibrate_pipeline_v2.py` | Grid-search classifier threshold and margin |
| `scripts/stress_pipeline_v2.py` | Ten-scenario stress matrix, including one full S6 run |
| `scripts/stress_clio_cusp_orchestra.py` | CLIO-CUSP stress harness |
| Git | Version control; runtime artifacts are kept outside the repository |

**Separate CLIO-CUSP kernel.** `app/math_engine/clio_cusp/` wraps the
repository-level `clio_cusp_8192_v4_kernel.py` and its stress harness. That path
uses NumPy/SciPy sparse linear algebra, optional PyTorch execution, TimesFM
time-series forecasting, Hugging Face Transformers for the ConfliBERT conflict
encoder, and python-igraph/leidenalg for signed community partitioning. It has
its own contracts and does not feed the pipeline v2 macro or household results;
its formulas are outside the scope of this document.

### 2.4 Design boundaries

- **Deterministic stages do not call an LLM.** S1's deterministic breakdown,
  S2's lexicon score, S3's cache write, S4's numerical engines, and S5's checks
  are code paths with explicit inputs and outputs. The LLM is optional in S1 and
  central in S6.
- **Thresholds live in configuration.** Classifier weights, revalidation
  thresholds, household coefficients, channel direction epsilons, and macro
  defaults are loaded from JSON or environment settings. Missing required files
  raise `ConfigError` rather than falling back to invented values.
- **No invented household data.** A channel with a missing required input is
  `not_estimated`; it carries no numeric magnitude.
- **No synthetic macro fallback.** If the World Bank panel cannot be built from
  live or cached real observations, the run fails. The engine does not replace a
  missing macro panel with a synthetic panel. The only synthetic data in the
  macro path is the optional firm-level satellite, and its mode is recorded.

---

## 3. Public and internal contracts

### 3.1 Request and mode contracts

`RawInput` is the pipeline entry point:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `run_id` | string | `run_<16 hex>` | Run identifier |
| `query` | string, non-empty | required | User question |
| `requested_mode` | `macro`, `micro`, `dual`, `auto` | `auto` | User's explicit choice |
| `personal_context` | `PersonalContext` or null | null | Optional personal context |
| `source_refs` | list of strings | `[]` | Caller-supplied citations |

`PersonalContext` contains only free text and categorical hints:

| Field | Default | Persisted only with opt-in |
|---|---|---|
| `free_text` | `""` | yes |
| `region` | `""` | yes |
| `occupation_sector` | `""` | yes |
| `household_notes` | `""` | yes |
| `assets_liabilities_notes` | `""` | yes |
| `persist_opt_in` | `false` | controls persistence |

`requested_mode` and `effective_mode` are distinct. The effective mode is one of
`macro`, `micro`, or `dual`. The mode router resolves `auto` as follows:

- If `requested_mode` is explicit, return it unchanged with source `explicit`.
- If the classifier label is `mixed`, or confidence is below the calibration
  threshold, require an explicit mode unless `allow_dual=true`; with
  `allow_dual=true`, run `dual` with source `auto_dual`.
- Otherwise use the classifier recommendation with source `classifier`.

### 3.2 Household profile

`HouseholdProfile` is separate from `PersonalContext` and carries explicit
numeric inputs. All fields are optional. Missing fields are not replaced by
defaults at the contract level; the engine decides which calculations are
possible.

| Field | Constraint | Unit |
|---|---|---|
| `monthly_income_million_vnd` | `> 0` | million VND/month |
| `monthly_expenditure_million_vnd` | `> 0` | million VND/month |
| `budget_shares_w0` | exactly 4 shares, each in `[0,1]`, sum to 1 | share |
| `n_adults` | integer `1..20` | people |
| `n_children` | integer `0..20` | people |
| `liquid_assets_million_vnd` | `>= 0` | million VND |
| `illiquid_assets_million_vnd` | `>= 0` | million VND |
| `debt_balance_million_vnd` | `>= 0` | million VND |
| `monthly_debt_service_million_vnd` | `>= 0` | million VND/month |
| `fx_assets_million_vnd` | `>= 0` | million VND |
| `fx_liabilities_million_vnd` | `>= 0` | million VND |
| `crra_rho` | `0 < rho <= 20` | dimensionless |
| `equity_risk_premium` | `[0,1]` | fraction |
| `return_variance` | `(0,1]` | variance |
| `psi_perm_pass` | `[0,1]` | pass-through |
| `phi_trans_pass` | `[0,1]` | pass-through |
| `beta_perm_macro` | exactly 5 loadings | dimensionless |
| `beta_trans_macro` | exactly 5 loadings | dimensionless |
| `equiv_method` | `oecd_modified`, `oecd_sqrt`, `bls_ses` | categorical |
| `poverty_line_z_monthly_million_vnd` | `> 0` | million VND/month |
| `notes` | string | free text |

`HouseholdProfile.from_personal_context()` calls `parse_household_numbers()` on
the free text. The parser recognizes Vietnamese and English phrases for income,
expenses, savings/liquid assets, debt service, debt balance, adults, and
children. It supports `nghìn/ngàn/k`, `triệu/tr/trillion/million/m`, and
`tỷ/ti/tỉ/billion/bn`. It parses only numbers that are explicitly written. A
missing amount remains `None`.

`merged_over(base)` returns a copy where non-null fields in the explicit profile
win over fields parsed from the personal context. A default `equiv_method` does
not override a deliberately supplied base method.

### 3.3 Cache states

The cache log uses six states and a strict transition graph:

```text
written ──► consumed_by_engine ──► revalidating ──► verified ──► purged
   │                 │                    │
   └─────────────────┴────────────────────┴──────► quarantined
```

`CACHE_TRANSITIONS` in `app/pipeline_v2/contracts.py`:

| From | Allowed next states |
|---|---|
| `written` | `consumed_by_engine`, `quarantined` |
| `consumed_by_engine` | `revalidating`, `quarantined` |
| `revalidating` | `verified`, `quarantined` |
| `verified` | `purged` |
| `purged` | none |
| `quarantined` | none |

Quarantined rows are never deleted by the revalidation module. A purge failure
leaves rows in `verified` and records `purge_error`.

### 3.4 Output contract

`PipelineOutput` is the final JSON object:

| Field | Meaning |
|---|---|
| `run_id` | Run identifier |
| `mode_used` | Effective mode |
| `status` | `ok`, `ok_with_warnings`, or `failed` |
| `macro` | `MacroResult` or null |
| `micro` | `MicroResult` or null |
| `validation` | `RevalidationReport` or null |
| `warnings` | List of human-readable warnings |
| `error` | Structured error or null |
| `classifier` | Classifier result or null |
| `orchestration` | S6 output summary or null |
| `artifacts` | Paths to run artifacts |
| `started_at`, `finished_at` | UTC timestamps |

The output never includes raw cache content or the personal context. Artifact
files are written outside the repository by default (`/tmp/osint_pipeline_v2`).

### 3.5 Noise and reproducibility controls

- The macro engine uses a fixed seed, default `20261026`, for PCA/VAR
  calibration, the Sobol sampler, the CMA-ES search, and the Wasserstein
  reduction.
- The macro artifact is canonicalized with an RFC 8785-compatible JSON
  canonicalization and hashed with SHA-256. The hash is stored as
  `artifact_sha256`.
- Every macro claim in `MacroResult` carries an `artifact:sha256:...` support
  reference and the cache-entry support collected in S3.
- S6 is not deterministic: LLM sampling, retries, and judge ordering can change
  its output. The deterministic stages still produce the same artifacts for the
  same inputs and seed.

---

## 4. Pipeline stages S1 to S7

### 4.1 S1: input and fetch

S1 builds a `RawMaterial` and a `QueryBreakdown`.

**Offline path (default).** The material is built from the query. The article
title is the first 180 characters of the query, `article_source` is
`user_query`, and a warning records that no live article was used. If an LLM
router is available, S1 may add topics and search phrases, but any numeric value
in the LLM response is discarded.

**Live path (`--live`).** S1 reuses `HeadlessEconArticleFetcher` and
`FullCircleWorkflow._analyze_and_filter`. It retries up to
`input_fetch.fetch_attempts` (default 2) and excludes already-tried URLs. The
article body is truncated to `max_article_chars` (default 6,000). If live fetch
fails and `require_live_article=false`, S1 falls back to the offline material
and records the failure. If `require_live_article=true`, it raises
`FetchError`.

**Deterministic breakdown.** The breakdown is assembled from:

- `classifier.macro_lexicon` and `classifier.micro_lexicon` term matches;
- `query_breakdown.indicator_terms` matches, keyed by World Bank code;
- `query_breakdown.personal_dimension_terms` matches;
- `parse_household_numbers()` on the query and on the context text;
- `query_breakdown.search_query_templates`, which default to:
  `"{query}"`, `"{query} Việt Nam số liệu mới nhất"`, and
  `"{query} tác động hộ gia đình"`.

The resulting `QueryBreakdown` has `method = "deterministic"` or
`"deterministic+llm"`. The LLM can add topics and search phrases; it cannot add
numeric parameters.

**Evidence filtering for live articles.** `filter_analysis_evidence()` keeps a
claim only when its normalized `evidence_quote` is at least 15 characters and
appears verbatim in the normalized article text. An indicator without a quote
is kept only when its numeric value is visible in the text. The evidence
integrity score is `1.0` if at least one claim survives and `0.0` otherwise.

`apply_word_filter()` then computes:

```text
lexical_score = min(1, (number_of_positive_terms + 0.5 * title_hits) / 8)
threshold_ok  = positive_terms >= 3 and lexical_score >= 0.35
analysis_ok   = analysis.macro_relevance >= 0.45 or lexical_score >= 0.60
evidence_ok   = claims_kept >= 1 and evidence_integrity >= 0.5
embedding_ok  = embedding_relevance is true
accepted      = threshold_ok and analysis_ok and evidence_ok
                and embedding_ok and no blocked negative term
```

The default positive lexicon is a Vietnamese macro vocabulary (inflation, GDP,
interest rate, exchange rate, credit, exports, imports, fiscal policy, and so
on). The default negative lexicon blocks advertising, promotion, PR, and
unverified-rumour language.

### 4.2 S2: classifier

The classifier is advisory. It combines a deterministic term score with an
embedding-prototype score.

Let `H_macro` be the number of distinct macro lexicon terms found in the
classification text, and `H_micro` the same for the micro lexicon. With
`lexicon_saturation = 5`:

```text
L_macro = min(1, H_macro / 5)
L_micro = min(1, H_micro / 5)
```

The embedding side uses the mean vector of the macro prototypes and the mean
vector of the micro prototypes:

```text
v_macro = mean(embed(p) for p in macro_prototypes)
v_micro = mean(embed(p) for p in micro_prototypes)
c_macro = cosine(embed(text), v_macro)
c_micro = cosine(embed(text), v_micro)
```

Cosine is in `[-1, 1]` and is mapped to `[0, 1]`:

```text
e_macro = (c_macro + 1) / 2
e_micro = (c_micro + 1) / 2
```

With `lexicon_weight = 0.55` and `embedding_weight = 0.45`:

```text
S_macro = 0.55 * L_macro + 0.45 * e_macro
S_micro = 0.55 * L_micro + 0.45 * e_micro
```

If embeddings are unavailable, the classifier falls back to
`S_macro = L_macro`, `S_micro = L_micro`, and records
`method = "lexicon"` plus a warning.

The decision uses the calibration file, not hard-coded constants:

- `threshold = 0.51`
- `mixed_margin = 0.02`

The effective runtime margin is the one in `config/classifier_calibration.json`.
`config/pipeline_v2.json` also declares `classifier.mixed_margin = 0.08`, but
`InputClassifier.classify()` reads `self.calibration.mixed_margin`, so the
calibration file controls the decision.

Decision rule:

| Condition | Label | Recommendation | Confidence |
|---|---|---|---|
| `S_macro >= threshold` and `S_micro >= threshold` | `mixed` | `dual` | `min(S_macro, S_micro)` |
| only macro above threshold | `macro` | `macro` | `S_macro` |
| only micro above threshold | `micro` | `micro` | `S_micro` |
| neither above, `abs(S_macro - S_micro) <= margin` | stronger side | stronger side | `max(S_macro, S_micro)` |
| neither above, macro > micro | `macro` | `macro` | `S_macro` |
| neither above, micro > macro | `micro` | `micro` | `S_micro` |

A confidence below the threshold causes `resolve_mode()` to require an explicit
mode unless `allow_dual=true`. This is deliberate: the classifier does not
silently choose a side when both scores are weak.

The calibration file was produced by
`scripts/calibrate_pipeline_v2.py` using a grid search over threshold and margin
on 46 labelled samples. Its recorded metrics are perfect on that sample set
(accuracy, balanced accuracy, and macro F1 all `1.0`, with a 15/16/15 confusion
matrix). That result describes the calibration sample, not production accuracy.

### 4.3 S3: cache log

S3 writes processed content to `cache_log_entries` in the existing SQLite
database. It does not write the raw article HTML or the personal context object.

The entries are:

1. the user query;
2. the query breakdown;
3. the article title and truncated body, if a live article exists;
4. the article summary and kept claim texts, if analysis exists.

When `persist_opt_in=false`, the query-breakdown entry is copied and its
`personal_dimensions` and `explicit_parameters` are emptied before writing. The
query itself is still written, because it is the user's request. The personal
context object itself never enters `RawMaterial`.

Embedding vectors are stored only if `cache_log.store_embedding_vectors=true`
(default). If embedding fails, the content is stored without a vector and a
warning is appended. Content is truncated to `max_content_chars` (default
20,000).

### 4.4 S4: engine execution

The mode router dispatches in dependency order:

```text
macro only : run_macro()
micro only : run_micro(None)
dual       : run_macro(); if successful, run_micro(macro_bundle)
```

**Macro adapter.** `run_macro()` builds a `MacroStressRequest` from the raw
material and analysis, executes `MacroStressEngineV57` in a worker thread, and
maps the hashed artifact to `MacroResult`. The adapter does not modify macro
internals.

The adapter's claims are Vietnamese templates over the artifact's active and
passive branch metrics, the regime stress probability, the gatekeeper acceptance
count, and the adversarial objective. Each claim is supported by up to five
cache-entry IDs plus `artifact:sha256:<hash>`.

The read-only micro payload contains:

```json
{
  "F_medoids_terminal": [...],
  "cluster_weights_q": [...],
  "optimal_policy_u": [0.0, 0.0, 0.0],
  "pi_stress_regime2": <stress_probability_last or 0.0>,
  "macro_snapshot": {
    "core_series": {...},
    "active": {...},
    "passive": {...},
    "tail_risk": [...],
    "artifact_sha256": "...",
    "request_id": "..."
  }
}
```

The zero policy vector is an explicit assumption, not an estimate. The adapter
records a warning saying so. `pi_stress_regime2` is the artifact's
`nowcast.regime.stress_probability_last`, not a separately estimated policy
stress.

**Micro engine.** `MicroChannelEngine` merges the explicit household profile
over the profile parsed from personal context and runs the seven configured
channels. If a macro bundle is present, it calls the household bridge with
`mode="single_individual_available"`; if not, all channels that need macro
shocks are `not_estimated` and a warning is recorded.

If the household bridge raises `MicroEngineError`, the engine catches it, adds
the message to `warnings`, and continues with `evaluation=None`. It does not
abort the run.

### 4.5 S5: revalidation and purge

S5 runs four checks. All four must pass before the cache can be verified and
purged.

#### Grounding

- Every macro claim must have at least one support item that is either a cache
  entry ID or a citation prefix (`artifact:`, `source:`, `doi:`, `http://`,
  `https://`, `world_bank:`).
- A micro channel with `magnitude_status = "computed"` must have
  `source_or_formula`.
- A micro channel with `magnitude_status = "sourced"` must have a citation or a
  cache-entry support.
- If `require_external_support=true`, at least one macro claim must cite an
  external source, not only cache entries.

The `grounding_similarity_threshold` field exists in
`config/pipeline_v2.json` and in the config model, but the current
`Revalidator._grounding()` does not use it. Grounding is an ID/citation check,
not a similarity check. This is documented here so operators do not mistake the
field for an active threshold.

#### Consistency

For each macro claim and each micro mechanism, S5 computes the maximum
similarity against the cache corpus.

If embeddings are available:

```text
sim(text, entry) = (cosine(embed(text), embed(entry)) + 1) / 2
score(item)      = max over entries of sim(item, entry)
threshold        = 0.28
```

If embeddings are unavailable, the fallback is token-overlap Jaccard after
Unicode normalization, diacritic stripping, and removal of one-character
tokens:

```text
Jaccard(A, B) = |A ∩ B| / |A ∪ B|
threshold     = 0.18
```

The check passes when the fraction of items whose score is at least the
threshold is at least `min_claim_support_ratio = 0.8`. The report records the
method (`embedding cosine (normalized [0,1])` or
`token-overlap Jaccard fallback`), the support ratio, and the threshold.

#### Completeness

- In `macro` or `dual` mode, a `MacroResult` must exist, its summary must be
  non-empty, and it must contain at least one claim with non-empty text.
- In `micro` or `dual` mode, a `MicroResult` must exist, its
  `transmission_chains` and `assumptions` must be non-empty, and every chain must
  have a non-empty `channel` and `mechanism`.
- Every `computed` or `sourced` chain must have both `magnitude_value` and
  `source_or_formula`.

#### Mode integrity

- `dual` requires both results, `micro.upstream_macro_used = true`, at least one
  `computed` channel, and at least one computed channel whose
  `source_or_formula` contains `artifact:`.
- `macro` must not return a `MicroResult`.
- `micro` must not return a `MacroResult`.

#### Purge

If any check fails, S5 calls `mark_quarantined(run_id)` and returns
`outcome = "not_purged_quarantined"`. Quarantined rows are not deleted.

If all checks pass, S5 calls `mark_verified(run_id)`. If
`cache_log.purge_enabled=true` (default), it then calls `purge_run(run_id)`.
`dry_run=true` uses an in-memory store and reports the purge candidate count
without an external write. A purge failure after verification leaves the rows
in `verified` and records `purge_error`.

### 4.6 S6: universal orchestrator

S6 runs `UniversalCoScientistPipeline`. It is the only stage that is inherently
LLM-driven. The pipeline has seven conceptual stages:

1. **Ingest, embed, and induce a dynamic ontology.** Articles are filtered with
   the contrastive score, indexed in ChromaDB, and summarized into a
   `TaskOntologySchema` with allowed predicates, positive rules, and conflict
   rules.
2. **Multi-model worker pool.** At least four workers generate hypotheses.
   Judges do not generate hypotheses.
3. **Embedding proximity and negative constraints.** Candidate hypotheses are
   compared for mode collapse, and negative constraints from the blackboard are
   injected into subsequent prompts.
4. **Dual critic and dynamic fuzzy Prolog.** A practical critic and a theoretical
   critic evaluate each hypothesis. The theoretical critic also proposes
   numerical bounds for the sandbox. The ProbLog scanner computes a viability
   score and a conflict score.
5. **Multi-judge Elo round-robin, hybrid fitness, and agent-evolver crossover.**
   Two or more judges compare hypotheses. Elo ratings are updated, hybrid fitness
   is computed, and the best parents are recombined.
6. **Universal parameter forcing sandbox and HMAC attestation.** The hypothesis
   is converted into a runnable check. Only an exit code of zero with all
   declared bounds satisfied earns an HMAC-SHA256 receipt and `verified_*`
   facts.
7. **JSON-schema normalization and Pareto export.** The final hypotheses are
   normalized to the output schema and exported with their fitness, Evidential
   Value, and risk metadata.

#### 4.6.1 Contrastive ingestion

The embedding engine uses positive and negative anchor vectors. For a document
vector `v`:

```text
s_contrast = cos(v, a_positive)
             - 0.50 * max(0, cos(v, a_negative) - 0.30)
```

A document is accepted when `s_contrast >= 0.42`. The positive anchor describes
sourced, quantitative, policy-relevant economics; the negative anchor describes
advertising, promotion, PR, unverified rumours, and sensational content.

#### 4.6.2 Temporal MMR and CRAG

Semantic search retrieves `min(max(3k, 6), available)` candidates, computes
cosine similarity, and selects up to `k` with temporal maximal marginal
relevance:

```text
decay(d) = exp(-0.005 * age_in_days)
MMR(d)   = 0.70 * decay(d) * cos(q, d)
           - 0.30 * max_{s in selected} cos(d, s)
```

The selected set is computed greedily. The time-decay coefficient is
`MMR_TIME_DECAY_GAMMA = 0.005` per day and `MMR_BETA = 0.70`.

CRAG uses the maximum query similarity:

| Maximum similarity | State | Action |
|---|---|---|
| `< 0.40` | low confidence | return an error hit requiring external search |
| `[0.40, 0.62)` | ambiguous | decompose selected documents into sentences, embed them, keep sentences with query similarity `>= 0.48`, and recompose |
| `>= 0.62` | correct | return the selected documents |

The numerical-divergence check groups numbers by unit, computes the coefficient
of variation `CV = std / mean` for each unit with at least two values, and flags
`DATA_CONFLICT_WARNING` when any `CV > 0.35`.

#### 4.6.3 ProbLog scoring

For hypothesis `H`, the scanner builds a dynamic ProbLog program from the
session ontology:

- each fact weight is clipped to `[0.001, 0.999]`;
- each positive rule contributes a `viable_clause`;
- each conflict rule, including compiled negative memory for the domain,
  contributes a `conflict` clause;
- predicates outside the ontology's allowed set are dropped;
- missing predicates used by a rule receive prior `0.001`;
- inference runs in a separate process with a 2.0-second timeout.

The net score is:

```text
Net_Fuzzy_Score(H) = P(viable(H)) * (1 - P(has_conflict(H)))
```

Status:

| Condition | Status |
|---|---|
| `P(has_conflict) > 0.65` | `NEEDS_REPAIR` |
| `Net_Fuzzy_Score < 0.20` | `REJECTED` |
| otherwise | `PASSED` |

A conflict clause with probability above `0.25` is included in
`conflict_trace`.

#### 4.6.4 Negative memory

Negative constraints detected in stage 4 are written to the short-term
blackboard with a `constraint_id`, `conflict_predicates`, and a Vietnamese
lesson. `NegativeMemoryCompiler` compiles them into ProbLog penalty rules per
`domain_context_tag`:

```text
weight = clip(base_weight * 0.60, 0.01, 0.99)
```

The default base weight is `0.99` and the decay factor is
`NEGATIVE_MEMORY_DECAY_FACTOR = 0.60`. If the same `constraint_id` appears again,
the stronger rule is kept, so the decay is not applied repeatedly. The compiled
file is `verifier_compiled_memory.json` by default.

#### 4.6.5 UCB1 model selection

For role `r` and model `m`, let `n_mr` be the number of trials of `m` in role
`r`, `n_r` the total trials in that role, and `mu_mr` the success rate. The
selection score is:

```text
UCB1(m, r) = mu_mr + 1.0 * sqrt(ln(n_r + 1) / n_mr)
```

An untried model has score `+inf`, so it is selected first. The exploration
constant is `UCB1_EXPLORATION_C = 1.0`.

#### 4.6.6 Elo and hybrid fitness

The blackboard starts each hypothesis at Elo `1200`. After a judged pairwise
comparison, with `K = 32`:

```text
E_winner = 1 / (1 + 10^((R_loser - R_winner) / 400))
E_loser  = 1 / (1 + 10^((R_winner - R_loser) / 400))
R_winner' = R_winner + 32 * (1 - E_winner)
R_loser'  = R_loser  + 32 * (0 - E_loser)
```

Hybrid fitness normalizes Elo within the current population so that a raw rating
of 1200 does not dominate the ProbLog score:

```text
norm_Elo(H) = (R_H - min(R)) / (max(R) - min(R) + 1e-9)
Fitness(H)  = 0.5 * norm_Elo(H) + 0.5 * Net_Fuzzy_Score(H)
```

The 0.5/0.5 weighting is the default `alpha` in `compute_hybrid_fitness()`.

#### 4.6.7 Sandbox attestation

A sandbox result is attested only when both conditions hold:

1. the process exit code is zero;
2. every declared metric lies inside its `MetricBoundSpec`.

The receipt is:

```text
digest  = SHA256(stdout)[:16]
payload = "<hypothesis_id>:universal_parameter_forcing:0:<digest>"
receipt = HMAC-SHA256(secret, payload)
```

The secret is `SOVEREIGN_SANDBOX_SECRET`, with a development fallback in the
code. On success, every computed metric is written as
`verified_<metric> = 0.95`. `sanitize_and_attest_facts()` strips any
`verified_*` predicate that does not carry a valid receipt, so an LLM cannot
promote its own claims to verified facts.

#### 4.6.8 S6 failure and timeout

S6 runs under `orchestrator.timeout_seconds = 2700.0` (45 minutes). The
reference local-Ollama run takes about 20 minutes. On timeout, the pipeline
keeps the S4 engine results, sets `orchestration = null`, adds a warning, and
returns `ok_with_warnings`. On any other S6 failure, the same fallback applies
with the exception message in `warnings`.

### 4.7 S7: output assembly

S7 writes `PipelineOutput` as JSON. The output includes the macro and micro
results, the validation report, classifier metadata, warnings, and artifact
paths. It does not include raw cache content or personal context. Secrets are
redacted before they reach logs or JSON.

### 4.8 Error model

Errors are structured with a stable code, a message, and a suggestion. The CLI
maps them to exit codes:

| Exit code | Meaning | Typical errors |
|---|---|---|
| `0` | success | none |
| `1` | internal | unexpected exception |
| `2` | invalid input/config | `INVALID_INPUT`, `INVALID_CONTEXT`, `CONFIG_INVALID`, `MODE_SELECTION_REQUIRED`, `PERSONAL_CONTEXT_REQUIRED` |
| `3` | external service | `FETCH_FAILED`, `LLM_FAILED`, `EMBEDDING_FAILED`, `SOURCE_UNAVAILABLE` |
| `4` | engine | `CLASSIFIER_FAILED`, `MACRO_FAILED`, `MICRO_FAILED`, `ENGINE_FAILED`, `CACHE_LOG_FAILED` |
| `5` | validation | `REVALIDATION_FAILED` |
| `6` | purge | `PURGE_FAILED` |

`redact_secrets()` replaces environment secret values (length at least 8) and
known secret patterns with `[REDACTED]` before errors are serialized.

## 5. Macro-financial engine v5.7

### 5.1 Layer map

`MacroStressEngineV57.run()` executes the following layers in order. Each layer
returns plain Python or NumPy values; only the final `MacroStressArtifact` is
serialized.

| Layer | Module | Function |
|---|---|---|
| 0 | `calibration.py` | Panel preparation, PCA, VAR(5), regime covariances, GPD tails, copula, Haar bank, transmission matrices |
| 1 | `rbpf.py` | Rao-Blackwellized particle filter and RTS smoother |
| 1.5 | `regimes.py` | Hamilton/Kim regime probability report and Haar sign audit |
| 2/3 | `adversarial.py` | Copula-GPD adversarial shock search and narrative weighting |
| 5 | `simulation.py` | Three-branch common-random-number Monte Carlo |
| 5.5 | `gatekeeper.py` | Joint gate, entropy pooling, tail risk, Wasserstein reduction |
| 6 | `gfevd.py`, `satellites.py`, `micro.py` | GFEVD, structural satellites, firm downscaling |
| Output | `engine.py` | Artifact assembly, hashing, timing, audit fields |

The engine returns `status = "FAILED"` with an `error` string instead of raising
on an internal exception. The pipeline adapter turns that into
`MacroEngineError`.

### 5.2 Panel and data provenance

The panel is fetched from the World Bank Indicators API by
`app/services/macro_stress.py`. It has exactly 36 columns and 30 annual rows
(1996–2025). The first nine columns are the core transmission block used by the
simulation and gatekeeper:

| Index | Code | Meaning | Unit | World Bank source | Transform |
|---:|---|---|---|---|---|
| 0 | `GDP_YOY` | Real GDP growth | % | `NY.GDP.MKTP.KD.ZG` | direct |
| 1 | `CPI_YOY` | CPI inflation | % | `FP.CPI.TOTL.ZG` | direct |
| 2 | `FX_DEPREC` | Nominal exchange-rate depreciation | % | `PA.NUS.FCRF` | year-over-year percent change |
| 3 | `IB_RATE` | Lending interest rate | % | `FR.INR.LEND` | direct |
| 4 | `FX_RESERVES` | Foreign-exchange reserves | billion USD | `FI.RES.TOTL.CD` | divide by \(10^9\) |
| 5 | `NPL_RATIO` | Non-performing loans | % of gross loans | `FB.AST.NPER.ZS` | direct |
| 6 | `CREDIT_GROWTH` | Domestic credit to private sector | % | `FS.AST.PRVT.GD.ZS` | year-over-year percent change |
| 7 | `CURRENT_ACCOUNT` | Current-account balance | billion USD | `BN.CAB.XOKA.CD` | divide by \(10^9\) |
| 8 | `FINANCIAL_ACCOUNT` | Financial-account balance | billion USD | `BN.FIN.TOTL.CD` | divide by \(10^9\) |

Columns 9–35 add context: per-capita growth, the GDP deflator, expenditure
shares, sector value added, population and labour indicators, health and
education, real interest rate, broad money, ODA, FDI, terms of trade, reserve
months, and the official exchange rate. Appendix A.2 lists all 36 columns with
their source codes.

Transform rules:

```text
direct      y_t = x_t
divide_1e9  y_t = x_t / 1e9
pct_change  y_t = 100 * (x_t / x_{t-1} - 1)
```

`pct_change` returns missing when the previous observation is missing or zero.
The panel keeps missing cells as `null`; it does not interpolate at fetch time.

**Cache behavior.** Each source indicator is cached as JSON under
`MACRO_STRESS_PANEL_CACHE_DIR` (default `./cache/world_bank_panel`). A cache
entry younger than `MACRO_STRESS_CACHE_TTL_HOURS = 168` is used without a
network call. On a failed or stale live fetch, the stale cache is used and the
status is recorded as `stale_cache:<ExceptionName>`. If neither live data nor a
cache exists, the fetch fails. The panel has 36 columns but 35 unique source
indicators: `PA.NUS.FCRF` supplies both `FX_DEPREC` and `EXCHANGE_RATE`. A fetch
is accepted only if at least 30 source indicators succeed, so at most five may
fail. A column with no observations at all is rejected with an explicit error
rather than synthesized.

**Missing-data policy.** In calibration, a column with at least three observed
values is linearly interpolated inside the observed span and filled with its
observed median outside it. A column with fewer than three observations is
filled entirely with its observed median. A column with zero observations raises
`ValueError("every panel series must have at least one observation")`. The
artifact records `missing_cells` and `missing_ratio`.

### 5.3 Layer 0: calibration

#### 5.3.1 Standardization and PCA

Let \(X \in \mathbb{R}^{T \times N}\) be the imputed panel, with \(T=30\) and
\(N=36\). Compute the column mean \(\mu_j\) and sample standard deviation
\(s_j\) (denominator \(T-1\)). If \(s_j \le 10^{-10}\), set \(s_j=1\). The
standardized panel is

\[
Z_{tj} = \frac{X_{tj} - \mu_j}{s_j}.
\]

Take the thin singular-value decomposition

\[
Z = U S V^{\top}.
\]

With \(r = \max(2, \min(r_{\text{requested}}, N, T - p - 2))\) and \(p=5\), the
default is \(r=5\). Factors and loadings are

\[
F = U_{[:,1:r]} S_{1:r}, \qquad \Lambda = V_{[:,1:r]},
\]

so \(Z \approx F \Lambda^{\top}\). The idiosyncratic variance is

\[
R_j = \max\left(\operatorname{Var}\left(Z_{\cdot j} - (F\Lambda^{\top})_{\cdot j}\right), 10^{-5}\right).
\]

The factor variance share reported in the diagnostics is

\[
\frac{\sum_{i=1}^{r} S_i^2}{\sum_{i=1}^{\min(T,N)} S_i^2}.
\]

The factors are centered by construction, so the VAR below has no intercept.

#### 5.3.2 VAR(5), companion form, and stability shrink

Fit a ridge-stabilized VAR with \(p=5\) lags:

\[
F_t = \sum_{\ell=1}^{p} A_\ell F_{t-\ell} + \varepsilon_t.
\]

The code solves

\[
\hat{B} = (X^{\top}X + 0.10 I)^{-1} X^{\top}Y,
\]

where \(X\) stacks \(F_{t-1},\ldots,F_{t-p}\) and \(Y\) stacks \(F_t\). The ridge
is `0.10`, not the `1e-4` default of the helper. The companion matrix is

\[
\mathcal{A} =
\begin{bmatrix}
A_1 & A_2 & \cdots & A_p\\
I & 0 & \cdots & 0\\
0 & I & \cdots & 0\\
\vdots & & \ddots & \vdots\\
0 & \cdots & I & 0
\end{bmatrix}
\in \mathbb{R}^{rp \times rp}.
\]

If the spectral radius \(\rho(\mathcal{A}) \ge 0.97\), every lag block is
multiplied by \(0.97/\rho(\mathcal{A})\) and the companion matrix is rebuilt.
The artifact records `var_companion_spectral_radius` and
`var_stability_shrink_applied`. The state dimension is \(rp = 25\).

The residual covariance is

\[
\Omega = \operatorname{Cov}(\hat{\varepsilon}) + 10^{-5} I_r.
\]

#### 5.3.3 Two-regime covariance mixture

The calibration uses a deterministic hard-EM approximation, not a full
Kim-collapsing EM:

1. Compute the residual volatility
   \(\nu_t = \sqrt{\sum_{i=1}^{r}\hat{\varepsilon}_{ti}^2}\).
2. Smooth it with a moving average of window
   \(w=\min(5,\max(2,\lfloor T/10\rfloor))\).
3. Label regime 0 when the smoothed volatility is at or below its median and
   regime 1 when above. If all labels are equal, split the sample at \(T/2\).
4. Initialize the transition matrix
   \[
   P^{(0)} = \begin{bmatrix}0.94 & 0.06\\0.18 & 0.82\end{bmatrix}.
   \]
5. For eight iterations:
   - estimate \(P\) from hard-label transitions, with every \(2\times2\) count
     initialized at one (Laplace smoothing);
   - run a Hamilton forward filter with initial prior \((0.5,0.5)\);
   - set each regime covariance to the sample covariance of residuals in that
     regime plus \(10^{-5}I_r\), falling back to the pooled covariance when the
     regime has too few observations;
   - relabel each period by the filtered probability argmax.

The Hamilton filter step is

\[
\pi_{t|t-1} = P^{\top}\pi_{t-1|t-1},
\]

\[
\pi_{t|t} =
\frac{\pi_{t|t-1}\odot
  \mathcal{N}(\hat{\varepsilon}_t;0,\Omega_{s})}
{\sum_{s=0}^{1}\pi_{t|t-1,s}\,
  \mathcal{N}(\hat{\varepsilon}_t;0,\Omega_{s})}.
\]

The reported `regime_probs` are these filtered probabilities; they are not
Kim-smoothed probabilities. The Cholesky factors used in simulation are

\[
L_s = \operatorname{chol}(\Omega_s + 10^{-7}I_r).
\]

#### 5.3.4 GPD marginal tails

For each of the first \(d=\min(4,r)\) residual series, fit a semi-parametric
marginal. Let \(x_{(1)}\le\cdots\le x_{(n)}\) be the sorted finite
observations. The threshold is the 90th percentile:

\[
u = Q_{0.90}(x).
\]

Let \(n_u\) be the number of observations strictly above \(u\), and

\[
p_u = \frac{n_u}{n+1}.
\]

The excesses \(x-u\) are fitted with a generalized Pareto distribution
(GPD) with location fixed at zero:

\[
G(y)=1-\left(1+\xi\frac{y}{\beta}\right)^{-1/\xi},
\qquad y>0,\; 1+\xi y/\beta>0.
\]

The fit uses `scipy.stats.genpareto.fit(excess, floc=0.0)`. The shape is clipped
to \(\xi\in[10^{-4},0.95]\) and the scale is floored at \(10^{-6}\). With fewer
than five excesses, the fit falls back to \(\xi=0.1\) and
\(\beta=\max(\operatorname{std}(\text{excess}),10^{-3})\), or \(\beta=1.0\)
when there are no excesses. With fewer than 12 observations, the whole marginal
is degenerate: `n_excess = 0`, `xi = 0.1`, `beta = 1.0`, the threshold is the
largest observed value, and the upper tail returns that value.

The CDF is C0-continuous at the threshold:

\[
F(x)=
\begin{cases}
\dfrac{\#\{x_i \le x\}}{n+1}, & x \le u,\\[4pt]
1-p_u\left(1+\xi\dfrac{x-u}{\beta}\right)^{-1/\xi}, & x>u.
\end{cases}
\]

The quantile function uses the empirical quantile (`np.quantile` with linear
interpolation) for \(p\le 1-p_u\), and

\[
Q(p)=u+\frac{\beta}{\xi}
\left[\left(\frac{1-p}{p_u}\right)^{-\xi}-1\right]
\]

for \(p>1-p_u\). If \(n_u=0\) or \(\xi\le0\), the upper tail returns the
largest observed value. The artifact reports `threshold`, `n_excess`, `xi`,
`beta`, and `tail_probability` for each marginal.

#### 5.3.5 Student-t copula correlation

For each pair of residual series \(i<j\), compute Kendall's \(\tau_{ij}\) and
map it to a Gaussian-copula correlation:

\[
\rho_{ij} = \sin\left(\frac{\pi \tau_{ij}}{2}\right).
\]

Non-finite \(\tau\) is set to zero. The matrix is symmetrized, its eigenvalues
are floored at \(0.05\), and it is rescaled to unit diagonal:

\[
R_c \leftarrow D^{-1/2} R_c D^{-1/2},
\qquad D_{ii} = (R_c)_{ii}.
\]

The result \(R_c\) is the correlation matrix used by the Mahalanobis
plausibility ellipsoid. It is not the covariance of the raw residuals.

#### 5.3.6 Haar rotation bank

For each regime \(s\in\{0,1\}\) and draw \(m=1,\ldots,64\), draw a
\(r\times r\) standard normal matrix, take its QR decomposition \(QR\), and
fix the signs so that the diagonal of \(R\) is positive:

\[
Q_m = Q\,\operatorname{diag}(\operatorname{sign}(R_{11}),\ldots,\operatorname{sign}(R_{rr})).
\]

The bank is fixed and audited, not selected. Layer 1.5 counts rotations whose
first column has a non-negative dot product with the GDP loading and whose third
column has a non-negative dot product with the FX loading. That count is
reported as `haar_sign_accepted` out of 128.

#### 5.3.7 Shock transmission and policy matrices

The factor model is mapped to the five core observables
`GDP_YOY`, `CPI_YOY`, `FX_DEPREC`, `IB_RATE`, `NPL_RATIO`. Let
\(\Lambda_c\in\mathbb{R}^{5\times r}\) be their loading rows. The exogenous
shock directions are defined by five target response rows:

\[
A_{\text{adverse}}=
\begin{bmatrix}
-0.90 & -0.35 & -0.55 & -0.65\\
 0.20 &  0.80 &  0.20 &  0.30\\
 0.85 &  0.15 &  0.25 &  0.65\\
 0.30 &  0.10 &  0.95 &  0.20\\
 0.50 &  0.20 &  0.60 &  0.40
\end{bmatrix}.
\]

Only the first \(d\) columns are used. The factor-space shock matrix is the
ridge projection

\[
B_\varepsilon =
(\Lambda_c^{\top}\Lambda_c + 0.01 I_r)^{-1}
\Lambda_c^{\top} A_{\text{adverse}}.
\]

The policy targets are stated at reference tool scales:

\[
A_{\text{policy}}=
\begin{bmatrix}
-0.08 &  0.015 &  0.16\\
 0.02 & -0.030 & -0.02\\
 0.04 & -0.160 & -0.10\\
 0.10 & -0.040 & -0.20\\
 0.04 &  0.000 & -0.06
\end{bmatrix},
\qquad
\text{scales}=(10,5,20).
\]

The per-unit policy matrix is

\[
B_u =
(\Lambda_c^{\top}\Lambda_c + 0.01 I_r)^{-1}
\Lambda_c^{\top} A_{\text{policy}}
\oslash \text{scales}.
\]

The shock decay vector is

\[
\rho = (0.85,\;0.82,\;0.42,\;0.88,\;0.80,\;0.75,\;0.78,\;0.82)_{1:d}.
\]

These are reduced-form reference coefficients. They are not estimated from the
panel and should not be read as structural elasticities.

The observation map from the companion state to all \(N\) series is

\[
\tilde{\Lambda} = [\Lambda,\;0,\ldots,0] \in \mathbb{R}^{N\times rp},
\]

so only the current factor block loads on observables.

### 5.4 Layer 1: RBPF and RTS smoother

The state space is conditionally linear given the regime:

\[
x_t = \mathcal{A}_{s_t} x_{t-1} + \eta_t,
\qquad \eta_t\sim\mathcal{N}(0,Q_{s_t}),
\]

\[
z_t = \tilde{\Lambda} x_t + \epsilon_t,
\qquad \epsilon_t\sim\mathcal{N}(0,\operatorname{diag}(R)).
\]

Here \(Q_s\) has \(\Omega_s\) in its first \(r\times r\) block and
\(10^{-7}I_{rp}\) on the full diagonal. The filter uses
`n_particles = min(256, max(64, requested_particles // 32))`, so the default
8,192-particle request produces a 256-particle filter.

Initialization: \(x_0=0\), \(P_0=0.6I_{rp}\), regimes drawn uniformly from
\(\{0,1\}\), weights uniform.

For each period \(t\):

1. Draw the next regime with
   \(P(s_t=1\mid s_{t-1})=P_{s_{t-1},1}\).
2. Predict
   \[
   x_{t|t-1}=\mathcal{A}_{s_t}x_{t-1|t-1},
   \qquad
   P_{t|t-1}=\mathcal{A}_{s_t}P_{t-1|t-1}\mathcal{A}_{s_t}^{\top}+Q_{s_t}.
   \]
3. Add a Bernoulli-Gaussian jump to each factor with probability `0.02`:
   \[
   x_{t|t-1}^{1:r} \mathrel{+}= \mathbf{1}\{U<0.02\}\,
   \mathcal{N}\!\left(0,\;1.5\sqrt{\operatorname{diag}(\Omega)}\right).
   \]
4. For the observed entries at \(t\), compute
   \[
   S_t = \tilde{\Lambda}_t P_{t|t-1}\tilde{\Lambda}_t^{\top}+\operatorname{diag}(R_t),
   \]
   \[
   K_t = P_{t|t-1}\tilde{\Lambda}_t^{\top}S_t^{-1},
   \]
   \[
   x_{t|t}=x_{t|t-1}+K_t(z_t-\tilde{\Lambda}_t x_{t|t-1}).
   \]
5. Update the covariance with the Joseph form
   \[
   P_{t|t}=(I-K_t\tilde{\Lambda}_t)P_{t|t-1}(I-K_t\tilde{\Lambda}_t)^{\top}
   +K_t\operatorname{diag}(R_t)K_t^{\top}.
   \]
6. Multiply the particle weight by the Gaussian likelihood
   \[
   w_t^{(p)} \propto w_{t-1}^{(p)}
   \exp\!\left(-\frac{1}{2}\left[n_t\log(2\pi)+\log|S_t|
   +\delta_t^{\top}S_t^{-1}\delta_t\right]\right).
   \]
7. Compute \(\mathrm{ESS}=1/\sum_p (w^{(p)})^2\). If
   \(\mathrm{ESS}<0.5N_p\), resample with replacement and reset the weights to
   uniform.

The backward RTS pass uses the realized regime at \(t+1\):

\[
J_t=P_{t|t}\mathcal{A}_{s_{t+1}}^{\top}P_{t+1|t}^{-1},
\]

\[
x_{t|T}=x_{t|t}+J_t(x_{t+1|T}-x_{t+1|t}),
\]

\[
P_{t|T}=P_{t|t}+J_t(P_{t+1|T}-P_{t+1|t})J_t^{\top}.
\]

The final smoothed state is the weighted mean of the particle states at the last
period. The nowcast is

\[
\hat{y} = \mu + s\odot(\tilde{\Lambda}\hat{x}_{T|T}).
\]

**Divergence guard.** If any nowcast value is non-finite, or if

\[
\max_j \frac{|\hat{y}^{\text{RBPF}}_j-\hat{y}^{\text{calib}}_j|}
{\max(|\hat{y}^{\text{calib}}_j|+1,1)} > 5,
\]

the engine discards the RBPF nowcast, uses the deterministic PCA/VAR state
`calib.last_state`, and records
`state_source = "calibration_nowcast_rbpf_divergence_fallback"`. Otherwise it
uses the smoothed state and records `"rbpf_rts_smoothed_state"`.

### 5.5 Layer 1.5: regime report

`run_regime_layer()` reports:

- `regime_probabilities_last`: the last row of the Hamilton filtered
  probabilities, normalized;
- `regime_probabilities_mean`: the time average of the filtered probabilities;
- `stress_probability_last`: the last filtered probability of regime 1;
- the transition matrix and regime counts;
- the Haar bank shape and structural sign acceptance count.

The engine uses `stress_probability_last` as the macro regime stress
probability. It does not run the endogenous Markov routine in `markov.py` for
the online simulation; the simulation uses the fixed `calib.p_trans` matrix.

### 5.6 Layers 2 and 3: adversarial shock search

#### 5.6.1 Plausibility ellipsoid

The search works in standardized factor-innovation space \(z\). A candidate is
projected onto the Mahalanobis ellipsoid

\[
z \leftarrow z\cdot\min\left(1,\frac{\kappa}{\sqrt{z^{\top}R_c^{-1}z}}\right).
\]

The radius is chosen so that the tail probability is \(\alpha=10^{-4}\):

\[
\kappa^2=
\begin{cases}
\operatorname{chi2.ppf}(1-\alpha,\;d), & d\notin\{3,4\},\\
21.1075, & d=3,\\
-2\,W_{-1}\!\left(-\dfrac{\alpha}{e}\right)-2, & d=4,
\end{cases}
\]

where \(W_{-1}\) is the real branch of the Lambert \(W\) function below \(-1\).
The dimension-4 expression is the exact solution of the order-2 Erlang
survival equation used by the reference calibration; the dimension-3 value is a
stored constant.

A candidate is mapped to a physical shock through the marginal distributions:

\[
u_j=\Phi(z_j), \qquad \varepsilon_j=Q_j(u_j),
\]

where \(Q_j\) is the GPD/ECDF quantile function of residual series \(j\).

#### 5.6.2 Objective

For each candidate, the engine propagates the companion state analytically
using the same \(B_\varepsilon\), \(B_u\), and decay vector as the simulation.
It computes four metrics:

\[
\text{gdp\_loss} = \min_h y^{\text{base}}_{\text{GDP},h}
                   - \min_h y^{\text{cand}}_{\text{GDP},h},
\]

\[
\text{fx\_deprec} = \max_h y^{\text{cand}}_{\text{FX},h}
                   - \operatorname{mean}_h y^{\text{base}}_{\text{FX},h},
\]

\[
\text{ib\_spike} = \max_h y^{\text{cand}}_{\text{IB},h}
                  - \max_h y^{\text{base}}_{\text{IB},h},
\]

\[
\text{npl\_delta} = \max_h y^{\text{cand}}_{\text{NPL},h}
                   - \max_h y^{\text{base}}_{\text{NPL},h}.
\]

Each metric is floored at \(-10\). The scalar objective is

\[
J(z)=0.35\,\text{gdp\_loss}+0.30\,\text{fx\_deprec}
     +0.25\,\text{ib\_spike}+0.10\,\text{npl\_delta}.
\]

Positive values mean a more adverse outcome, so the optimizer maximizes \(J\).

#### 5.6.3 CMA-ES

The optimizer is a compact implementation of Hansen's covariance-matrix
adaptation. Defaults: population \(\lambda=32\), generations \(G=50\),
initial mean \(m_0=0\), initial step size \(\sigma_0=2.0\), initial covariance
\(C_0=I\). The number of parents is \(\mu=\lfloor\lambda/2\rfloor=16\), with
logarithmic weights

\[
w_i = \frac{\log(\mu+0.5)-\log i}{\sum_{j=1}^{\mu}
      \bigl(\log(\mu+0.5)-\log j\bigr)},
\qquad i=1,\ldots,\mu.
\]

The effective selection mass is
\(\mu_{\text{eff}}=1/\sum_i w_i^2\). The standard learning rates are

\[
c_c=\frac{4+\mu_{\text{eff}}/d}{d+4+2\mu_{\text{eff}}/d},
\qquad
c_s=\frac{\mu_{\text{eff}}+2}{d+\mu_{\text{eff}}+5},
\]

\[
c_1=\frac{2}{(d+1.3)^2+\mu_{\text{eff}}},
\qquad
c_\mu=\min\left(1-c_1,\;
\frac{2(\mu_{\text{eff}}-2+1/\mu_{\text{eff}})}
{(d+2)^2+\mu_{\text{eff}}}\right),
\]

\[
d_\sigma=1+2\max\left(0,\sqrt{\frac{\mu_{\text{eff}}-1}{d+1}}-1\right)+c_s,
\qquad
\chi_d=\sqrt{d}\left(1-\frac{1}{4d}+\frac{1}{21d^2}\right).
\]

At each generation, the code samples

\[
z_i = m+\sigma\,C^{1/2}\xi_i,\qquad \xi_i\sim\mathcal{N}(0,I),
\]

projects each \(z_i\) onto the ellipsoid, evaluates \(J\), selects the \(\mu\)
best, updates the mean, the evolution paths \(p_\sigma\) and \(p_c\), the
covariance \(C\), and the step size

\[
\sigma \leftarrow \sigma\exp\left[
\frac{c_s}{d_\sigma}\left(\frac{\|p_\sigma\|}{\chi_d}-1\right)\right],
\]

with \(\sigma\) clipped to \([0.05,8]\). The step-size path uses the indicator

\[
h_\sigma=
\mathbf{1}\left[
\frac{\|p_\sigma\|}{\sqrt{1-(1-c_s)^{2(g+1)}}}
<
(1.4+2/(d+1))\chi_d
\right].
\]

The best 64 unique candidates across all generations are retained, ranked by
objective, and the top 16 form the elite set. If the elite set is short, it is
filled with Gaussian perturbations of the best candidate.

#### 5.6.4 Narrative weights

The elite set receives soft weights that combine copula plausibility and
economic-sign penalties. With \(z\) the standardized shock,

\[
m^2 = z^{\top}R_c^{-1}z,
\qquad
K(z)=\left(1+\frac{m^2}{\nu}\right)^{-(\nu+d)/2},
\quad \nu=5.
\]

For each candidate, the sign violations are

\[
v=(\max(0,-\text{gdp\_loss}),\;
   \max(0,-\text{fx\_deprec}),\;
   \max(0,-\text{ib\_spike}),\;
   \max(0,-\text{npl\_delta})),
\]

and the penalty is

\[
\Pi=\frac{1}{2}\left[
\left(\frac{v_1}{0.75}\right)^2+
\left(\frac{v_2}{1.50}\right)^2+
\left(\frac{v_3}{1.00}\right)^2+
\left(\frac{v_4}{0.50}\right)^2
\right].
\]

The raw narrative weight is \(K(z)e^{-\Pi}\), normalized across the elite set.
The weights are used to allocate simulation particles to shock archetypes; they
are not probabilities of the shock occurring.

The output records `mode`, `shock_dimension`, the Mahalanobis radius, the best
standardized and physical shock, the best metrics and objective, the elite
arrays, the narrative weights, and the number of objective evaluations.

### 5.7 Layer 5: three-branch simulation

#### 5.7.1 Common random numbers

The simulation uses \(N_p=8{,}192\) particles and horizon \(H=4\). The Sobol
sampler is Owen-scrambled with dimension \(rH=20\). It draws
\(2^{\lceil\log_2 N_p\rceil}\) points, clips uniforms to
\([10^{-12},1-10^{-12}]\), and maps them through the normal quantile:

\[
z_{n,h,j}=\Phi^{-1}(U_{n,h,j}).
\]

The first half of the particles is the center stratum and is left unshifted.
The second half is the tail stratum and receives the deterministic twist

\[
\mu_{\text{twist}}=(-0.85,\;-0.60,\;0.75,\;-0.90,\;0.80,\;0.60)_{1:r}.
\]

The raw Radon–Nikodym weight is

\[
\log R_n=\operatorname{clip}\left(
\sum_{h=1}^{H}\left(z_{n,h}\cdot\mu_{\text{twist}}
-\frac{1}{2}\|\mu_{\text{twist}}\|^2\right),-50,50
\right),
\]

\[
w_n^{\text{raw}}=\frac{1}{0.5+0.5\,e^{\log R_n}}.
\]

The weights lie in \((0,2]\). They are not normalized inside the generator;
normalization happens on the valid subset in Layer 5.5.

All three branches use the same \(z\), the same regime uniforms, the same Haar
rotation index, and the same particle-to-shock assignment. This is what makes
the passive/active comparison causal rather than a comparison of unrelated
samples.

#### 5.7.2 Shock allocation

The 16 elite shocks are assigned to particle blocks in proportion to their
narrative weights. Let \(q_k\) be the normalized weight of elite shock \(k\).
The cumulative boundaries are

\[
b_k=\left\lfloor N_p\sum_{j=1}^{k}q_j\right\rfloor,
\qquad b_K=N_p.
\]

Particles in block \(k\) use the physical shock vector of elite member \(k\)
for all four horizons, multiplied by the decay

\[
\text{shock}_{n,h}=\varepsilon_{k(n)}\odot
\left(\rho_1^{h+1},\ldots,\rho_d^{h+1}\right).
\]

The baseline branch uses zero shock and zero policy. The passive and active
branches use the same shock. The active branch additionally applies the policy
rule described below.

#### 5.7.3 State propagation

For each branch and horizon \(h=0,\ldots,H-1\):

1. Draw the next regime from `p_trans`:
   \[
   s_{n,h}=\mathbf{1}\{U_{n,h}\ge P_{s_{n,h-1},0}\}.
   \]
2. Propagate the companion state:
   \[
   x_{n,h}=\mathcal{A}_{s_{n,h}}x_{n,h-1}+L_{s_{n,h}}z_{n,h}.
   \]
3. Add the shock and policy effects to the first \(r\) coordinates:
   \[
   x_{n,h}^{1:r}\mathrel{+}=B_\varepsilon\,\text{shock}_{n,h}
   +B_u\,u_{n,h}.
   \]
4. Map to observables:
   \[
   y_{n,h}=\mu+s\odot(\tilde{\Lambda}x_{n,h}).
   \]

The baseline branch sets \(u=0\) and keeps the interbank rate at its previous
value. The passive branch also sets \(u=0\), but applies the interest-rate
smoothing

\[
i^{\text{passive}}_{n,h}=\operatorname{clamp}
\left(i_{n,h-1}-0.35(i_{n,h-1}-4.5),\;1,\;25\right).
\]

#### 5.7.4 Active policy rule

The active branch has three policy tools: `bills`, `delta_res` (FX intervention
in billion USD), and `omo`. The default archetype is `EMDE_IPF_CORRIDOR`. Let

\[
\operatorname{pos}(x)=\max(x,0),\qquad
\operatorname{clamp}(x,a,b)=\min(\max(x,a),b).
\]

For the default corridor:

\[
\text{fx\_breach}=\operatorname{pos}(fx-2.3),
\]

\[
\text{bills}=\operatorname{clamp}
\left(1.0\,\operatorname{pos}(5-i)+2.0\,\text{fx\_breach},\;0,\;30\right),
\]

\[
\text{avail}=\operatorname{pos}(\text{reserves}-\text{reserves\_min}-0.5),
\]

\[
\text{fx\_sold}=\min\left(1.5\,\text{fx\_breach}
+0.5\,\operatorname{pos}(\text{shock}_1),\;\text{avail}\right),
\]

\[
\text{delta\_res}=-\text{fx\_sold},
\]

\[
\text{omo}=\operatorname{clamp}
\left(8\,\operatorname{pos}(i-4.2)
+10\,\operatorname{pos}(\text{shock}_2)
+8\,\operatorname{pos}(\text{shock}_3),\;0,\;50\right).
\]

The interbank rate update is

\[
i'=\operatorname{clamp}\left(
i-0.35(i-4.5)+0.01\,\text{bills}-0.04\,\text{omo}
-0.06|\text{delta\_res}|+0.01\,\text{drain},\;1,\;25
\right),
\]

with \(\text{drain}=10\max(\text{shock}_3,-5)\).

The two alternative archetypes are:

- `FREE_FLOATING_RESERVE`: no FX intervention, zero reserve change,
  \(\text{bills}=\operatorname{clamp}(1.2\,\operatorname{pos}(5-i),0,30)\),
  \(\text{omo}=\operatorname{clamp}(5\,\operatorname{pos}(i-5),0,60)\).
- `HARD_PEG_CURRENCY_BOARD`: no bills or OMO,
  \(\text{fx\_sold}=\min(2.5\,\operatorname{pos}(fx-0.1),
  \operatorname{pos}(\text{reserves}-0.5))\),
  \(\text{delta\_res}=-\text{fx\_sold}\).

The active policy vector stored in the artifact is
\(u=(\text{bills},\text{delta\_res},\text{omo})\).

#### 5.7.5 Stock-flow consistency

The reserve identity is

\[
\Delta \text{Res}_t = \text{CA}_t+\text{FA}_t.
\]

The model first computes the factor-implied reserve change
\(\Delta\text{Res}^{\text{endo}}_t\), adds the active intervention
\(\Delta\text{Res}^{\text{policy}}_t\), and applies the Doran projection with
tolerance \(\varepsilon_{\text{SFC}}\le 4.5\):

\[
\delta=\Delta\text{Res}-\text{CA}-\text{FA},
\]

\[
\text{excess}=\operatorname{sign}(\delta)\max(|\delta|-\varepsilon_{\text{SFC}},0),
\]

\[
\Delta\text{Res}\leftarrow\Delta\text{Res}-0.4\,\text{excess},
\quad
\text{CA}\leftarrow\text{CA}+0.3\,\text{excess},
\quad
\text{FA}\leftarrow\text{FA}+0.3\,\text{excess}.
\]

The reserve stock is then clipped at an absorbing floor:

\[
\text{Res}_{t+1}=\max(\text{Res}_{\min},\text{Res}_t+\Delta\text{Res}).
\]

The clipping residual is split equally between the current and financial
accounts:

\[
\text{CA}\leftarrow\text{CA}+0.5(\text{Res}_{t+1}-\text{Res}_t-\Delta\text{Res}),
\]

\[
\text{FA}\leftarrow\text{FA}+0.5(\text{Res}_{t+1}-\text{Res}_t-\Delta\text{Res}).
\]

In the passive branch, a reserve shortfall below the floor is allowed to raise
FX depreciation by

\[
fx\leftarrow fx+0.45\max(0,\text{Res}_{\min}-\text{Res}^{\text{unclipped}}_{t+1}).
\]

The floor is `reserves_min = 10.0` billion USD by default. The initial reserve
stock is the nowcast value, floored at `reserves_min`; if the nowcast is
non-finite or non-positive, the code uses `88.5` billion USD.

### 5.8 Layer 5.5: gate, weights, and reduction

#### 5.8.1 Joint validity gate

For each branch, a path is valid only if all three conditions hold.

**Physical domain:**

\[
\text{finite}(y)\;\wedge\;
\text{Res}_h\ge 5\;\wedge\;
\text{NPL}_h\in[0.2,35]\;\wedge\;
\text{IB}_h\in[0.1,40]
\quad\forall h.
\]

**Stock-flow consistency:**

\[
\left|\Delta\text{Res}_h-(\text{CA}_h+\text{FA}_h)\right|\le \tau
\quad\forall h,
\]

where \(\tau\) is the request's SFC tolerance (default 5.0). Note that the
simulation's internal Doran projection uses `min(4.5, tau)` while the gate uses
`tau`.

**Economic transmission sign:** reject a path when

\[
\operatorname{mean}_h \text{IB}_h>10
\;\wedge\;
\max_h \text{FX}_h>5
\;\wedge\;
\text{NPL}_{H}<1.5.
\]

That combination is treated as economically implausible: a large interest-rate
and exchange-rate stress without any terminal NPL deterioration.

The joint set is the intersection of the valid sets of baseline, passive, and
active. If it contains fewer than `max(50, k_rep)` particles, the gate falls
back to the active branch's valid set; if that is still too small, it accepts
all particles. The fallback is recorded with `fallback_used = true`. The
acceptance rate is the fraction of particles in the joint set.

#### 5.8.2 Entropy pooling

Let \(p_i\) be the normalized raw simulation weight on the valid set. The
engine imposes one constraint on terminal baseline GDP:

\[
\sum_i p_i\,g_i = \bar{g}.
\]

If the request supplies `qpm_anchor_gdp_yoy_pct`, that value is the target.
Otherwise

\[
\bar{g}=\sum_i p_i g_i-\max\left(0.10,\;0.05\,\operatorname{std}(g)\right).
\]

The exponential tilt is

\[
p_i(\lambda)=\frac{p_i e^{\lambda(g_i-\bar{g})}}
{\sum_j p_j e^{\lambda(g_j-\bar{g})}}.
\]

The code solves for \(\lambda\) by bisection on \([-100,100]\) for 160
iterations. The posterior is solved once on the baseline branch and frozen for
all branches. This is the saturated one-constraint special case of the dual
entropy-pooling problem.

#### 5.8.3 Kish effective sample size floor

The posterior may be concentrated on a few particles. The engine enforces

\[
\mathrm{ESS}=\frac{1}{\sum_i p_i^2}\ge \min(200,\;0.95N_{\text{valid}}).
\]

It tries the blend

\[
p^{(\alpha)}=\alpha p^{\text{posterior}}+(1-\alpha)p^{\text{prior}},
\qquad
\alpha\in\{1,0.9,0.75,0.6,0.5,0.35,0.2,0\},
\]

and accepts the first \(\alpha\) whose ESS meets the floor. If none does, it
returns uniform weights. The artifact records `kish_ess_before_floor`,
`kish_ess`, `kish_blend_alpha`, and the floor.

#### 5.8.4 Weighted VaR and ES

For a loss vector \(L\) sorted in ascending order with weights \(w\), and tail
probability \(\alpha=0.99\):

1. Find the first index \(k\) with \(\sum_{i\le k}w_i\ge\alpha\).
2. Set \(\mathrm{VaR}_\alpha=L_k\).
3. Let the atom at the boundary be
   \(a=\max(0,\sum_{i\le k}w_i-\alpha)\).
4. Compute the tail mass
   \[
   T=a L_k+\sum_{i>k}w_i L_i.
   \]
5. Report
   \[
   \mathrm{ES}_\alpha=\frac{T}{1-\alpha}.
   \]

The `tail_sign` field flips the sign for series where a low value is adverse
(GDP, reserves, credit). The artifact's `tail_risk` table reports mean, standard
deviation, VaR 99, ES 99, sign, and unit for all 36 series on the active branch.

#### 5.8.5 Branch metrics

For each branch, with posterior weights \(p\):

- `gdp_yoy_mean_pct`: weighted mean terminal GDP growth.
- `gdp_yoy_trough_mean_pct`: weighted mean of the minimum GDP growth over the
  horizon.
- `gdp_yoy_std_pct`: weighted standard deviation of terminal GDP growth.
- `gdp_tail_loss_var_pct` and `gdp_tail_loss_es_pct`: VaR/ES of
  \(\text{baseline mean GDP}-\text{branch GDP trough}\).
- `fx_deprec_var_pct`: VaR of terminal FX depreciation.
- `fx_reserves_drain_billion_usd`:
  \(\max(0,\text{initial reserves}-\text{weighted terminal reserves})\).
- `interbank_peak_p95_pct`: weighted 95th percentile of the maximum interbank
  rate over the horizon.
- `npl_breach_probability`: weighted probability that peak NPL exceeds `3.5`.
- `npl_terminal_mean_pct`: weighted mean terminal NPL.
- `valid_particle_count` and `acceptance_rate`.

#### 5.8.6 Wasserstein reduction to 50 archetypes

The active branch's valid paths are reduced to \(K_{\text{rep}}=50\) weighted
medoids. Let \(y_{i,h,j}\) be the path of particle \(i\), horizon \(h\), series
\(j\). The procedure is:

1. Compute weighted means and standard deviations across particles for each
   \((h,j)\).
2. Standardize and apply a time discount:
   \[
   \tilde{y}_{i,h,j}=
   \frac{y_{i,h,j}-\bar{y}_{h,j}}{s_{h,j}}\,0.95^{h}.
   \]
3. Flatten each particle to a vector of length \(H\cdot N\).
4. Compute the weighted first principal direction by eight power iterations:
   \[
   v\leftarrow \frac{X^{\top}(p\odot(Xv))}{\|X^{\top}(p\odot(Xv))\|}.
   \]
5. Score each particle by \(s_i=\tilde{y}_i\cdot v\).
6. Sort by score and choose \(M=250\) candidate quantiles on the weighted
   distribution, using the grid
   \(0.5/M,1.5/M,\ldots,1-0.5/M\).
7. Greedily select \(K_{\text{rep}}\) medoids from the candidates to minimize
   the weighted sum of nearest-medoid Euclidean distances. This is a discrete
   Kantorovich/\(k\)-medoids objective, not an exact Wasserstein barycenter.
8. Assign each particle to its nearest selected medoid and sum its weight to
   obtain `cluster_weights`.
9. Return the terminal factor vector of each selected medoid as
   `F_medoids_terminal`, which is the only macro object the household engine
   consumes.

The artifact reports the medoid indices, cluster weights, Kish ESS, candidate
count, and a `degenerate` flag when there are no more particles than medoids.

### 5.9 Layer 6: GFEVD, satellites, and firm downscaling

#### 5.9.1 Generalized forecast-error variance decomposition

The GFEVD is computed in observables space, not in factor space. Let

\[
\Sigma_y=\Lambda\bar{\Omega}\Lambda^{\top}+\operatorname{diag}(R),
\qquad
\bar{\Omega}=\frac{\Omega_0+\Omega_1}{2}.
\]

The factor moving-average matrices are

\[
\Phi_0=I_r,
\qquad
\Phi_h=\sum_{\ell=1}^{p} A_\ell \Phi_{h-1-\ell},
\]

with \(\Phi_{h}=0\) for negative indices. The projection matrix from factors to
observables is

\[
W=\left(\Lambda^{\top}\operatorname{diag}(R)^{-1}\Lambda+10^{-7}I_r\right)^{-1}
\Lambda^{\top}\operatorname{diag}(R)^{-1}.
\]

For each horizon \(h\),

\[
\Psi_h=\Lambda\Phi_h W,
\qquad
P_h=\Psi_h\Sigma_y.
\]

The numerator and denominator accumulate

\[
\text{num}_{ij}\mathrel{+}=\frac{(P_h)_{ij}^2}{(\Sigma_y)_{jj}},
\qquad
\text{den}_{i}\mathrel{+}=\sum_{k}(P_h)_{ik}(\Psi_h)_{ik}.
\]

The raw decomposition is

\[
\theta_{ij}=\frac{\text{num}_{ij}}{\text{den}_i},
\]

and the reported matrix is row-normalized to 100:

\[
\text{GFEVD}_{ij}=100\,\frac{\theta_{ij}}{\sum_j\theta_{ij}}.
\]

The artifact contains the full \(36\times36\) matrix and the top 80
source-target edges by variance contribution. This is the generalized
(Koop–Pesaran–Potter) decomposition; it does not require orthogonalized shocks.

#### 5.9.2 Structural satellites

Satellites are evaluated on the 50 medoid terminal factor vectors and their
cluster weights. The coefficients below are reference values, not estimated
structural parameters.

**Dynamic Nelson–Siegel yield curve** (Diebold, Rudebusch, and Aruoba, 2006):

\[
\text{level}=3.20+0.16 f_1+0.08 f_j,
\qquad
j=\begin{cases}5, & r>4,\\ 1, & \text{otherwise}.\end{cases}
\]

\[
\text{slope}=0.95+0.22 f_2-0.12 f_1,
\qquad
\text{curvature}=0.40+0.10 f_3.
\]

For maturities \(\tau\in\{2,5,10\}\) years and \(\lambda=0.65\),

\[
x=\lambda\tau,\qquad
\frac{1-e^{-x}}{x}\;\text{and}\;
\frac{1-e^{-x}}{x}-e^{-x},
\]

\[
y(\tau)=\text{level}+\text{slope}\,
\frac{1-e^{-x}}{x}
+\text{curvature}\left(\frac{1-e^{-x}}{x}-e^{-x}\right).
\]

**Bohn (1998) sovereign debt snowball.** With initial debt `35.0`% of GDP,
effective rate \(r_{\text{eff}}=6.0+0.10f_2+0.05f_3\), primary balance
\(pb=1.0+0.05f_1\), and stock-flow adjustment \(sfa=0.20+0.05\,fx\),

\[
d_{t+1}=
\frac{1+r_{\text{eff}}/100}
{\max(1+(gdp+cpi)/100,\,0.5)}\,d_t
-pb+sfa.
\]

**Adrian–Shin (2010) bank capital channel.** With initial CAR `11.5`%,
\(\Delta npl=0.10f_1+0.05f_3+0.02f_4\), and
\(nim=2.5+0.05f_2\),

\[
car=11.5+0.25\,nim-0.65\max(\Delta npl,0),
\]

\[
\text{credit ceiling}=3.5\max(car-8.0,0).
\]

The satellite output reports weighted p05, p50, p95, and mean for each
quantity, the share of medoids below the 8% Basel minimum, and the scenario
weights.

#### 5.9.3 Firm-level downscaling

The macro engine writes an uncompressed Arrow IPC firm table and reads it with
zero-copy column access. If `firm_data_path` is supplied and exists, the mode is
`provided_arrow_ipc`; otherwise the engine creates a synthetic fixture with
`firm_count = 1600` and records `synthetic_fixture`. The synthetic fixture uses:

\[
dvi\sim\operatorname{Beta}(2.0,3.5),\qquad
icr\sim\operatorname{LogNormal}(0.35,0.45),
\]

\[
\Pr(\text{SME})=0.62,
\]

\[
\beta_1\sim\mathcal{N}(0.55,0.30),\;
\beta_2\sim\mathcal{N}(0.20,0.25),\;
\beta_3\sim\mathcal{N}(0.35,0.45),
\]

\[
\beta_4\sim\mathcal{N}(-0.30,0.25),\;
\beta_5\sim\mathcal{N}(0.15,0.35),
\]

\[
\text{sens}_{u1}\sim\mathcal{N}(0,0.05),\;
\text{sens}_{u2}\sim\mathcal{N}(0,0.08),\;
\text{sens}_{u3}\sim\mathcal{N}(0,0.06).
\]

The cross-sectional shape quartet is:

\[
\bar{dvi}_p=\frac{\sum_i \max(w_i,0)\,dvi_i}{\sum_i\max(w_i,0)},
\]

\[
\text{dispersion}=p_{90}(dvi)-p_{10}(dvi),
\]

\[
\text{Kelley skew}=\frac{(p_{90}-p_{50})-(p_{50}-p_{10})}{p_{90}-p_{10}},
\]

\[
\text{critical mass}=\operatorname{mean}
\left[\mathbf{1}\{dvi>0.80\;\wedge\;icr<1.25\}\right].
\]

For firm \(i\), medoid \(k\), and policy tool \(u_m\),

\[
\text{stress}_{ik}=dvi_i+\sum_{m=1}^{3}\text{sens}_{u_m,i}u_m
+\text{endo\_skew}\cdot\text{SME}_i
+\sum_{m=1}^{5}\beta_{m,i}F_{k,m},
\]

where

\[
\text{endo\_skew}=0.15\,\text{Kelley skew}\,
(1+0.80\,\pi_{\text{stress}}).
\]

The expected stress is

\[
\bar{s}_i=\sum_k q_k\,\text{stress}_{ik},
\]

and the 99% weighted quantile over \(k\) is the firm-level
`predicted_stress_var99_next_q`. The output reports p05, p50, p95, and mean for
both the expected and VaR99 distributions, and the shares above `0.80`. The
`pi_stress_regime2` input is
`nowcast.regime.stress_probability_last` from the macro artifact.

This firm layer is a deterministic satellite. It does not feed back into the
macro branches or into the household bridge.

### 5.10 Output and audit fields

The hashed artifact contains:

- `status`, `engine_version`, `request_id`, `request_sha256`;
- `started_at`, `finished_at`, execution time, peak RSS;
- `dimensions` (series, factors, companion dimension, lags, regimes, Haar
  draws, particles, valid particles, acceptance rate, medoids, firm count,
  and the reported unrolled equation counts);
- `calibration` (backend, imputation policy, factor count, VAR lags, spectral
  radius and shrink flag, missing cells, regime counts, transition matrix,
  factor variance share, GPD parameters, copula correlation, shock decay);
- `nowcast` (core series, state source, RBPF divergence flag, smoothed factor,
  regime report, RBPF diagnostics);
- `adversarial_shock`, `branches`, `gatekeeper`, `tail_risk`, `wasserstein`,
  `gfevd`, `gfevd_matrix`, `structural_satellites`, `micro_downscaling`;
- `deterministic_briefing`, `audit`, and `artifact_sha256`.

The artifact hash is computed over the artifact payload with the
`artifact_sha256` field removed. The canonicalization is described as
RFC 8785-compatible JSON canonicalization plus SHA-256. The hash detects
accidental modification; it is an unkeyed content hash, not a digital
signature and not proof of the producer's identity.

### 5.11 Known approximations in the macro engine

These are explicit in the code and should be repeated in any review:

- Layer 0 is `pca_var5_hard_em_covariance`, a deterministic approximation to a
  full EM factor model.
- The regime filter is hard-label EM with a Hamilton forward filter, not Kim's
  collapsing smoother.
- The copula is a Gaussian copula on Kendall-transformed correlations. It is
  used for the plausibility ellipsoid, not for generating the simulation shocks;
  simulation shocks are Sobol-normal with a deterministic tail twist.
- The policy matrices and satellite coefficients are reference calibrations,
  not estimated structural parameters.
- The simulation uses the fixed transition matrix `calib.p_trans`. The
  endogenous Markov routine in `markov.py` is not called by
  `run_counterfactual_simulation`.
- The firm table is synthetic unless a real Arrow IPC file is supplied.
- `optimal_policy_u` is not published by the hashed artifact. The pipeline
  adapter passes `[0,0,0]` and records that as an assumption; the macro engine
  itself computes an internal `optimal_policy_u` as the mean active policy, but
  that value is not part of the adapter payload.
- `pi_stress_regime2` is the last filtered regime-1 probability from the
  nowcast, not a separately estimated policy-stress variable.

## 6. Household micro engine

### 6.1 Scope and input gating

The household subsystem is an on-demand calculator, not a population model
fitted to Vietnam. It takes an explicit household profile and, optionally, the
macro engine's medoid payload. It returns three blocks:

- `cost_of_living_evaluation`: price projection, QUAIDS share update, index
  numbers, subsistence floor, Hicksian CV;
- `income_and_buffer_stock_security`: BPP income projection, Carroll–Toche
  target, liquid buffer, MDSR, HANK class, MPC, Merton share, EGM kink, VEP;
- `distributional`: FGT P0/P1/P2 when the vulnerability block is available.

The bridge has three entry modes:

| Mode | Requirement | Behavior |
|---|---|---|
| `single_individual` | core, income, and balance fields all present | Raises if any required field is missing |
| `single_individual_available` | any subset | Evaluates only the blocks whose inputs are present; null otherwise |
| `population_cross_section` | Arrow IPC population table and macro payload | Vectorized evaluation over households |

`app/pipeline_v2/micro_engine.py` always calls
`single_individual_available`, so a missing income or balance field disables the
corresponding block instead of failing the run.

The core fields are `monthly_expenditure_million_vnd`, `budget_shares_w0`,
`n_adults`, and `n_children`. The income field is
`monthly_income_million_vnd`. The balance fields are
`liquid_assets_million_vnd`, `illiquid_assets_million_vnd`, and
`monthly_debt_service_million_vnd`. If a core field is missing, the entire
household evaluation is `null` and all channels that depend on it are
`not_estimated`.

### 6.2 Macro-to-micro price projection

The macro payload supplies \(K=50\) terminal factor vectors
\(F_k\in\mathbb{R}^5\), cluster weights \(q_k\), a policy vector
\(u=(u_1,u_2,u_3)\), and a regime stress probability \(\pi_s\). Prices are
normalized to one before the shock:

\[
p_{0,g}=1,\qquad g=1,\ldots,4.
\]

The log price change for good \(g\) is

\[
\Delta\ln p_g =
\sum_{m=1}^{5}F_{k,m}M_{g,m}
+\underbrace{(-0.012)\sum_{j=1}^{3}u_j}_{\text{policy dampener}}
+\underbrace{0.025\,\pi_s}_{\text{regime markup}}.
\]

The post-shock price is

\[
p_{1,g}=\exp\left(\operatorname{clip}(\Delta\ln p_g,\;-0.35,\;0.65)\right).
\]

The \(4\times5\) matrix \(M\) in `config/micro_household.json` is

\[
M=
\begin{bmatrix}
0.025 & 0.010 & 0.048 & -0.008 & 0.032\\
0.012 & 0.035 & 0.022 &  0.015 & 0.010\\
0.065 & 0.008 & 0.055 &  0.012 & 0.045\\
0.010 & 0.018 & 0.020 &  0.025 & -0.015
\end{bmatrix}.
\]

Rows are goods in the order **Food, Housing, Energy/Transport,
Discretionary/Services**. Columns are factors in the order **Global,
Liquidity, FX/Inflation, Real Growth, GPR/Sentiment**. The matrix is a
transmission calibration, not an estimated input-output table.

Because the same policy vector and stress probability apply to every good, the
policy and regime terms shift all four log prices by the same amount. They
change the overall price level but not relative prices.

The pipeline adapter always passes `optimal_policy_u = [0,0,0]`, because the
macro artifact does not publish the internal policy vector. In a normal
pipeline run, the policy dampener is therefore zero. The non-zero policy
example in Appendix A.6.1 exercises the household engine directly and is not
what `dual` mode supplies.

### 6.3 QUAIDS share update

The demand system has four goods. The coefficient restrictions are checked when
the engine is constructed:

\[
\sum_g \alpha_g=1,\qquad
\sum_g \beta_g=0,\qquad
\sum_g \lambda_g=0,\qquad
\Gamma=\Gamma^{\top}.
\]

The configured values are

\[
\alpha=(0.42,0.24,0.12,0.22),
\]

\[
\beta=(-0.160,-0.104,0.029,0.235),
\]

\[
\lambda=(-0.012,-0.008,0.005,0.015),
\]

\[
\Gamma=
\begin{bmatrix}
-0.045 & 0.015 & 0.010 & 0.020\\
 0.015 & -0.030 & 0.008 & 0.007\\
 0.010 & 0.008 & -0.025 & 0.007\\
 0.020 & 0.007 & 0.007 & -0.034
\end{bmatrix}.
\]

For a price vector \(p\), the translog price index is

\[
\ln a(p)=\alpha_0+\sum_g \alpha_g\ln p_g
+\frac{1}{2}\sum_g\sum_h \gamma_{gh}\ln p_g\ln p_h,
\]

with \(\alpha_0=1.0\), and the Cobb–Douglas scaling term is

\[
b(p)=\exp\left(\sum_g \beta_g\ln p_g\right).
\]

Let \(x\) be nominal monthly expenditure, \(D_h\) the equivalence scale, and
\(w_0\) the observed base budget shares. The code computes the real
expenditure term

\[
\ln \tilde{x}=\ln\left(\frac{x}{D_h}\right)-\ln a(p_1).
\]

The post-shock share is a first-order QUAIDS update around the observed shares:

\[
w_{1,g}=w_{0,g}
+\sum_h \gamma_{gh}\ln\left(\frac{p_{1,h}}{p_{0,h}}\right)
+\beta_g\ln\tilde{x}
+\frac{\lambda_g}{b(p_1)}(\ln\tilde{x})^2.
\]

The result is clipped to \([0.005,0.985]\) and renormalized to sum to one. This
is an update of the observed shares, not a full re-estimation of the demand
system from prices and total expenditure. The distinction matters: the base
shares carry household-specific information that the four-good QUAIDS
coefficients cannot reproduce.

The configured coefficients satisfy the adding-up, homogeneity, and symmetry
restrictions, but they are illustrative placeholders. `config/micro_household.json`
states that they require recalibration with VHLSS/VES data before production
use.

### 6.4 Equivalence scales

The household equivalence scale \(D_h\) converts nominal expenditure into
per-equivalent-adult terms. Three methods are available:

\[
D_h^{\text{OECD modified}}=
1+0.5(n_a-1)+0.3n_c,
\]

\[
D_h^{\text{OECD sqrt}}=\sqrt{n_a+n_c},
\]

\[
D_h^{\text{BLS SES}}=
1+0.267(n_a-1)+0.099n_c.
\]

Here \(n_a\ge1\) is the number of adults and \(n_c\ge0\) the number of children.
The scale is adjusted for the relative food price:

\[
D_{h,k}=D_h\left(\frac{p_{1,\text{food},k}}{p_{0,\text{food}}}\right)^{\eta},
\qquad
\eta=0.08\,\frac{n_c}{n_a+n_c}.
\]

The exponent is zero for a household with no children. The adjustment makes
the scale increase when food becomes more expensive relative to the base,
because children are assumed to have a higher food share. The coefficient
`eta_child_food = 0.08` is a calibration assumption.

### 6.5 Index numbers and Hicksian variation

With base shares \(w_0\), post-shock shares \(w_1\), and price relatives
\(r_g=p_{1,g}/p_{0,g}\):

**Törnqvist–Theil:**

\[
\ln P_T=\frac{1}{2}\sum_g (w_{0,g}+w_{1,g})\ln r_g,
\qquad
P_T=\exp(\ln P_T).
\]

**Laspeyres:**

\[
P_L=\sum_g w_{0,g}r_g.
\]

**Paasche:**

\[
P_P=\frac{1}{\sum_g w_{1,g}(1/r_g)}.
\]

**Fisher:**

\[
P_F=\sqrt{P_LP_P}.
\]

The code labels Laspeyres as the upper bound and Paasche as the lower bound. In
the usual cost-of-living comparison, Laspeyres overstates and Paasche
understates the true index when preferences are stable. The Fisher index is the
geometric mean of the two.

The subsistence floor is

\[
E_{\text{sub}}=D_h\sum_g p_{1,g}\gamma_g^{\text{LES}},
\]

with \(\gamma^{\text{LES}}=(1.80,1.20,0.45,0.35)\). These are subsistence
quantities, not expenditures; multiplying by prices gives a monthly floor in
million VND.

The reported Hicksian compensating variation is

\[
CV_h=x\,(P_T-1).
\]

This is a first-order approximation, not the exact expenditure-function
difference. It measures the extra nominal expenditure needed to restore the
pre-shock utility level at the Törnqvist price index. The exact Hicksian CV
would require solving the QUAIDS expenditure function at the reference utility,
which the current code does not do.

Real equivalized consumption is

\[
x^{\text{real}}=\frac{x}{D_hP_T}.
\]

### 6.6 BPP income process and partial insurance

The BPP block implements the Blundell–Pistaferri–Preston (2008) permanent and
transitory income decomposition and a partial-insurance projection.

#### 6.6.1 Covariance identification

Let \(\Delta y_{i,t}\) be log residual income growth for household \(i\) in
period \(t\). The moment conditions are cross-sectional at each period, not
within-household over time. For \(T\ge3\),

\[
\hat{\sigma}_{u,t}^2=
\max\left(10^{-5},-\widehat{\operatorname{Cov}}_i
(\Delta y_{i,t},\Delta y_{i,t-1})\right),
\]

\[
\hat{\sigma}_{v,t}^2=
\max\left(10^{-5},\widehat{\operatorname{Cov}}_i
(\Delta y_{i,t},\Delta y_{i,t-1}+\Delta y_{i,t}+\Delta y_{i,t+1})\right).
\]

The cross-sectional covariance is centered across households at each period.
The reported scalar estimates are the period averages:

\[
\hat{\sigma}_u^2=\frac{1}{T-2}\sum_t \hat{\sigma}_{u,t}^2,
\qquad
\hat{\sigma}_v^2=\frac{1}{T-2}\sum_t \hat{\sigma}_{v,t}^2.
\]

Here \(v\) is the permanent shock variance and \(u\) the transitory shock
variance. The function returns both the scalar estimates and the per-period
vectors for diagnostics. Fewer than three periods raises `ValueError`.

In the online single-household path, these variances are not estimated from
panel data. The BPP projection uses configured pass-through coefficients
instead. The estimator exists for a caller that supplies a panel.

#### 6.6.2 Conditional projection

Given five macro factors \(F_k\), base nominal income \(y_0\), Törnqvist price
index \(P_T\), permanent loading vector \(\beta_v\), transitory loading vector
\(\beta_u\), permanent pass-through \(\psi\), and transitory pass-through
\(\phi\):

\[
v_k=\beta_v^{\top}F_k,
\qquad
u_k=\beta_u^{\top}F_k-0.35\ln P_{T,k}.
\]

The consumption growth projection is

\[
\Delta\ln c_k=\psi\,v_k+\phi\,u_k.
\]

The post-shock real income is

\[
y^{\text{real}}_k=\frac{y_0}{P_{T,k}}
\exp(v_k+u_k).
\]

The coefficient `0.35` is `bpp_price_transitory_pass_through`: a 1% price
increase reduces the transitory income component by 0.35%. The default
loadings are

\[
\beta_v=(-0.015,-0.010,-0.012,0.045,-0.018),
\]

\[
\beta_u=(-0.020,-0.035,-0.025,0.030,-0.030),
\]

and the default pass-throughs are \(\psi=0.78\), \(\phi=0.18\). A household
profile can override them. The projection does not model the full BPP
consumption-insurance regression; it applies the configured partial-insurance
coefficients directly.

### 6.7 Carroll–Toche target buffer

The Carroll–Toche target is a dimensionless ratio of market resources to
permanent income, not a number of months. The code computes:

\[
\thorn_R=R^{(1/\rho)-1}\beta^{1/\rho},
\qquad
\thorn_\Gamma=\frac{(R\beta)^{1/\rho}}{\Gamma},
\]

with \(R\) gross return, \(\beta\) discount factor, \(\rho\) CRRA, and \(\Gamma\)
income growth. Both are capped below at \(0.995\) before use:

\[
\tilde{\thorn}_R=\min(0.995,\thorn_R),
\qquad
\tilde{\thorn}_\Gamma=\min(0.995,\thorn_\Gamma).
\]

The return-impatience and growth-impatience gaps are

\[
\kappa_u=1-\tilde{\thorn}_R,
\qquad
\vartheta=1-\tilde{\thorn}_\Gamma.
\]

The income-growth log gap and prudence term are

\[
\gamma_{\ln}=\ln\left(\max\left(1.0001,\frac{1}{\tilde{\thorn}_\Gamma}\right)\right),
\qquad
\omega=\frac{1}{2}(\rho-1).
\]

With unemployment probability \(\mho\),

\[
\text{den}=\vartheta\left[
1+\frac{\gamma_{\ln}}{\mho}
\left(1-\frac{\gamma_{\ln}}{\mho}\omega\right)
\right],
\]

floored at \(0.02\). The target ratio is

\[
m_{\text{target}}=1+\frac{1}{\text{den}}.
\]

The block also reports `ric_satisfied = thorn_R < 1` and
`gic_satisfied = thorn_Gamma < 1`. The default parameters are \(R=1.01\),
\(\beta=0.985\), \(\Gamma=1.005\), \(\rho=2.5\), and unemployment probability
\(0.03+0.04\pi_s\).

### 6.8 Endogenous grid method

The EGM solves a compact consumption-saving problem with a borrowing limit and
a lognormal income innovation. It uses five Gauss–Hermite nodes

\[
x=(-2.02018287,-0.95857246,0,0.95857246,2.02018287),
\]

with weights

\[
\varpi=(0.01995324,0.39361932,0.94530872,0.39361932,0.01995324)/\sqrt{\pi}.
\]

The income nodes are

\[
\xi_j=\exp\left(\sqrt{2}\,\sigma_\xi x_j-\frac{1}{2}\sigma_\xi^2\right).
\]

The asset grid is

\[
a_j=\exp(\operatorname{linspace}(0,\ln(1+a_{\max}),n))-1,
\]

with \(n=80\) and \(a_{\max}=24\). Let \(b\) be the borrowing limit. The code
maintains endogenous grids \(m_j\) and \(c_j\), initialized as

\[
a_j'=a_j-b,
\qquad
m_j=a_j'+0.5+0.1a_j,
\qquad
c_j=0.5+0.1a_j.
\]

Each iteration computes

\[
m'_{j,k}=\frac{R}{\Gamma}a_j'+\xi_k,
\]

\[
c'_{j,k}=
\begin{cases}
\max(10^{-6},m'_{j,k}+b), & m'_{j,k}\le m_0,\\
\operatorname{interp}(m'_{j,k};m,c), & \text{otherwise},
\end{cases}
\]

\[
\mathbb{E}[\mu_{j}]=\sum_k \varpi_k\Gamma^{-\rho}
\max(10^{-6},c'_{j,k})^{-\rho},
\]

\[
c_j^{\text{new}}=\left(\beta R\,\mathbb{E}[\mu_j]\right)^{-1/\rho},
\qquad
m_j^{\text{new}}=c_j^{\text{new}}+a_j'.
\]

The loop stops when \(\max_j|c_j^{\text{new}}-c_j|<10^{-6}\), or after 120
iterations. The reported `m_kink_threshold` is the first endogenous grid point
\(m_0\), the point at which the borrowing constraint stops binding. The solver
returns the endogenous grid and consumption function; the single-household
output reports only the kink threshold.

The EGM is a reference solver with one asset, no permanent-income state, and a
five-node income shock. It is not the full Carroll (2006) model with a
permanent-income grid and a continuous income distribution.

### 6.9 Two-asset HANK classification and Merton share

The HANK block uses the Kaplan–Moll–Violante (2018) two-asset logic. It
computes:

\[
\text{liquid buffer months}=
\frac{b_{\text{liquid}}}{\max(10^{-6},E_{\text{sub}})},
\]

\[
\text{supernumerary income}=
\max(10^{-6},x-E_{\text{sub}}),
\]

\[
\text{MDSR}=
\frac{\text{monthly debt service}}{\text{supernumerary income}}.
\]

A household is liquid-constrained when

\[
\text{buffer months}<3.0
\quad\text{or}\quad
\text{MDSR}>0.30.
\]

The three regimes are:

| Code | Label | Condition |
|---:|---|---|
| 0 | `Unconstrained_Buffer_Stock_Saver` | not constrained |
| 1 | `Wealthy_Hand_to_Mouth_W_HtM` | constrained and illiquid assets \(>0\) |
| 2 | `Poor_Hand_to_Mouth_P_HtM` | constrained and illiquid assets \(\le0\) |

The quarterly MPC is a regime lookup:

\[
\text{MPC}=
\begin{cases}
0.055, & \text{code }0,\\
0.28, & \text{code }1,\\
0.52, & \text{code }2.
\end{cases}
\]

The Merton optimal risky-asset share is

\[
\omega^*=
\begin{cases}
\operatorname{clip}\left(\dfrac{erp}{\rho\sigma_r^2},0,1\right),
& \text{buffer months}\ge3,\\[6pt]
0, & \text{otherwise}.
\end{cases}
\]

The equity risk premium and return variance are scenario-specific:

\[
erp_k=\operatorname{clip}
\left(0.055+0.012F_{k,4}-0.018F_{k,2},\;0.01,\;0.14\right),
\]

\[
\sigma_{r,k}^2=\operatorname{clip}
\left(0.032+0.015\pi_s+0.008|F_{k,1}|,\;0.015,\;0.12\right).
\]

A household profile may override `equity_risk_premium` and `return_variance`.
The reported share is the weighted average over the 50 scenarios. The
`expected_quarterly_mpc` label is deliberate: the MPC values are quarterly
objects even though the surrounding household budget is monthly.

### 6.10 FGT poverty and vulnerability

The poverty line is \(z=3.50\) million VND per month by default. For real
equivalized consumption \(x_i\), the household-level Foster–Greer–Thorbecke
components are

\[
P_0(x)=\mathbf{1}\{x<z\},
\]

\[
P_1(x)=\max\left(0,\frac{z-x}{z}\right),
\]

\[
P_2(x)=\left[\max\left(0,\frac{z-x}{z}\right)\right]^2.
\]

For scenario \(k\), the vulnerability-to-poverty probability is

\[
VEP_{i,k}=
\Phi\left(
\frac{\ln z-\ln x_{i,k}}{\sigma_{c,k}}
\right),
\]

where

\[
\sigma_{c,k}=0.14+0.06\pi_s.
\]

The expected VEP is

\[
\overline{VEP}_i=\sum_k q_k VEP_{i,k},
\]

and the tail VEP is the weighted 99th percentile of \(VEP_{i,\cdot}\) (the
worst 1% of scenarios). The aggregate FGT indices are household averages of the
scenario-weighted values:

\[
\text{FGT}_0=\frac{1}{N_h}\sum_i\sum_k q_k P_0(x_{i,k}),
\]

and analogously for \(P_1\) and \(P_2\). A household is counted as vulnerable
when \(\overline{VEP}_i\ge0.292\) and highly fragile when
\(\overline{VEP}_i\ge0.500\). These thresholds are configuration values, not
estimated poverty lines.

The normal-CDF vulnerability formula treats log consumption as normal with
known standard deviation \(\sigma_{c,k}\). It is a closed-form approximation,
not a simulated consumption distribution.

### 6.11 The seven transmission channels

`config/micro_channels.json` defines seven channels. `MicroChannelEngine.run()`
executes them in order. A channel is `computed` only when its required inputs
are present; otherwise it is `not_estimated` and carries no numeric magnitude.
The direction vocabulary is:

- `negative`: adverse for the household;
- `positive`: favorable;
- `none`: inside the neutral dead band;
- `ambiguous`: no defensible sign because inputs are missing or the mechanism
  is indeterminate.

The default dead bands are:

| Channel | Epsilon | Unit |
|---|---:|---|
| `prices` | 0.0001 | index units |
| `income_employment` | 0.01 | percent change |
| `borrowing_cost` | 0.005 | million VND/month |
| `savings_return` | 0.0005 | million VND/month |
| `exchange_rate` | 0.005 | million VND |
| `assets_liabilities` | 0.0001 | used only by the generic fallback |
| `public_transfers` | 0.0001 | unused |

#### 6.11.1 Prices

If the household bridge returns a cost-of-living block, the channel uses the
expected Törnqvist index and Hicksian CV:

\[
\text{change}=100(P_T-1).
\]

Direction is `negative` when \(P_T>1+\epsilon\), `positive` when
\(P_T<1-\epsilon\), and `none` otherwise. The magnitude text reports the
percentage change, \(P_T\), and the CV in million VND per month. The source
string cites the index and CV equations plus the macro artifact hash.

If the bridge is unavailable but the macro snapshot has `CPI_YOY`, the channel
reports the CPI as context, marks `not_estimated`, and uses the sign of CPI as
the direction. Otherwise the direction is `ambiguous`.

#### 6.11.2 Income and employment

If the bridge returns expected real disposable income, the channel compares it
with the household's stated monthly income:

\[
\text{change}=100\left(\frac{y^{\text{real}}}{y_0}-1\right).
\]

Direction is `negative` below \(-1\%\), `positive` above \(+1\%\), and `none`
inside the band. The magnitude reports both amounts. The source cites the BPP
equations and the artifact hash.

If income is missing, the channel falls back to the sign of `GDP_YOY` as
context and marks `not_estimated`.

#### 6.11.3 Borrowing cost

The channel requires the stressed interbank P95 peak, the baseline `IB_RATE`,
and monthly debt service. It assumes a floating-rate loan whose rate moves
one-for-one with the interbank rate:

\[
D_{\text{stress}}=D_0\left(1+\frac{r_{\text{stress}}-r_0}{100}\right).
\]

The change is \(D_{\text{stress}}-D_0\). Direction is `negative` when the change
exceeds the 0.005 million VND dead band, `positive` when it is below the
negative band, and `none` otherwise. The mechanism string states the
floating-rate assumption explicitly.

This is an interest-rate pass-through calculation, not a full amortization
schedule. It does not change the principal or the remaining maturity.

#### 6.11.4 Savings return

The channel requires liquid assets, the stressed interbank rate, the baseline
interbank rate, and CPI. It computes the real rate before and after:

\[
r^{\text{real}}_0=r_0-\text{CPI},
\qquad
r^{\text{real}}_{\text{stress}}=r_{\text{stress}}-\text{CPI},
\]

\[
\Delta_{\text{monthly}}=
\text{liquid}\times
\frac{r^{\text{real}}_{\text{stress}}-r^{\text{real}}_0}{100\times12}.
\]

Direction is `positive` above the 0.0005 million VND band, `negative` below the
negative band, and `none` otherwise. The mechanism assumes liquid assets earn a
rate close to the interbank rate. The calculation is a monthly interest-income
change, not a compounded return.

#### 6.11.5 Exchange-rate exposure

The channel requires the active-branch FX depreciation VaR and at least one of
FX assets or FX liabilities:

\[
\text{net FX position}=\text{FX assets}-\text{FX liabilities},
\]

\[
\text{impact}=\text{net FX position}\times\frac{\text{FX shock}}{100}.
\]

Direction is `positive` when the impact exceeds the 0.005 million VND band,
`negative` when it is below the negative band, and `none` otherwise. A positive
net position benefits from depreciation; a net liability loses. The channel
does not model hedging, currency-matched income, or valuation timing.

#### 6.11.6 Assets and liabilities

If the bridge returns both liquid buffer months and MDSR, the direction is
`negative` when

\[
\text{buffer months}<3.0
\quad\text{or}\quad
\text{MDSR}>0.30,
\]

and `positive` otherwise. The magnitude reports buffer months, MDSR, the HANK
class, and expected quarterly MPC. The source cites the HANK and Merton
equations plus the artifact hash.

If the bridge is unavailable but liquid assets and monthly expenditure are
present, the channel computes the static buffer

\[
\text{months}=\frac{\text{liquid assets}}{\text{monthly expenditure}},
\]

with the same 3.0-month threshold, and marks the result `computed` with the
formula `liquid_assets / monthly_expenditure`. This static fallback is not a
HANK classification.

#### 6.11.7 Public transfers

The channel is always `not_estimated` and `ambiguous`. The macro artifact does
not provide a household-level transfer amount or incidence. The mechanism text
explains that fiscal and social policy can support or retard household income
depending on who receives it, and appends the first two deterministic briefing
lines as context. No numeric magnitude is invented.

### 6.12 Bridge output blocks and aggregation

For a single household, the scenario-weighted cost-of-living block reports:

- expected Törnqvist, Fisher, Laspeyres, and Paasche indices;
- expected subsistence floor in million VND;
- expected Hicksian CV in million VND;
- expected real equivalized consumption;
- the scenario-weighted post-shock QUAIDS shares.

The income and buffer block reports:

- expected real disposable income (if income is present);
- Carroll–Toche target ratio and its units;
- actual liquid buffer months;
- MDSR;
- modal HANK classification;
- expected quarterly MPC;
- Merton risky-asset share;
- EGM borrowing kink threshold;
- expected VEP and tail VaR99 VEP.

The distributional block reports aggregate FGT P0, P1, and P2. If the HANK
block is unavailable, the FGT block is null even when income is present, because
the current code computes VEP inside the HANK branch.

For a population Arrow IPC table, the bridge reads `x_nominal`, `n_adults`,
`n_children`, `b_liquid`, `a_illiquid`, `debt_service`, `rho_crra`, and the four
base shares with zero-copy column access. It returns population FGT indices,
vulnerable and high-fragility shares, poor-HtM and wealthy-HtM shares,
population-weighted quarterly MPC, and a distributional quartet:

\[
\text{median VEP},\quad
IQR=Q_{75}-Q_{25},\quad
\text{Kelley skew}=
\frac{(Q_{90}-Q_{50})-(Q_{50}-Q_{10})}{Q_{90}-Q_{10}},
\]

and the share with \(\overline{VEP}\ge0.500\). This quartet is the feedback
object used by the firm downscaling layer.

### 6.13 Calibration status and limitations

Every coefficient in `config/micro_household.json` is a model parameter, not a
household observation. The file's own `source_note` says the coefficients are
illustrative calibration placeholders from the micro specification and require
VHLSS/VES recalibration before production use.

The most consequential limitations are:

- The QUAIDS share update is local to the observed base shares. It is not a
  full demand-system solution.
- The Hicksian CV is \(x(P_T-1)\), a first-order approximation.
- The BPP variances are only estimated when a panel is supplied. The online
  path uses configured pass-through coefficients.
- The EGM is a compact one-asset solver with a five-node income shock.
- The HANK classification is a threshold rule, not a solved heterogeneous-agent
  model.
- The Merton share is a myopic mean-variance formula, not a dynamic portfolio
  solution.
- VEP is a normal-CDF approximation with a scenario-invariant log-consumption
  variance.
- The seven channels use partial-equilibrium arithmetic. They do not solve for
  general-equilibrium feedback between household demand, prices, and income.

## 7. Economic theory map

This section maps each theory to the place where the code uses it. The formulas
are in Sections 5 and 6; this section explains the economic logic and the main
limitation of each application.

### 7.1 Macro-financial theories

| Theory | Reference | Where used | What the code actually does | Main limitation |
|---|---|---|---|---|
| Factor models and approximate factor structure | Stock and Watson (2002) | Layer 0 PCA | SVD of the standardized panel; first five factors | Linear, static loadings, no dynamic factor structure |
| Vector autoregression and companion form | Sims (1980) | Layer 0 VAR(5), Layer 5 propagation | Ridge VAR on PCA factors, companion matrix, spectral-radius shrink | Linear, constant parameters within regime, short annual sample |
| Markov regime switching | Hamilton (1989); Kim (1994) | Layer 0 covariance mixture, Layer 1.5 | Two hard-labelled covariance regimes with a Hamilton forward filter | Hard-label EM approximation, no Kim smoother in calibration |
| State-space filtering | Kalman (1960); Rao-Blackwellization | Layer 1 RBPF/RTS | Conditional Kalman update per particle, RTS backward pass | Particle collapse possible; divergence guard falls back to deterministic nowcast |
| Extreme value theory | Balkema and de Haan (1974); Pickands (1975) | Layer 0 GPD tails | Peaks-over-threshold GPD above the 90th percentile | Small annual sample; GPD fit can be unstable |
| Copula dependence | Sklar (1959) | Layer 0 copula correlation, Layer 3 plausibility | Gaussian copula from Kendall's tau | Elliptical dependence, no tail asymmetry beyond the t-kernel |
| Mahalanobis plausibility | Mahalanobis (1936) | Layer 2 shock projection | Project candidates onto a tail-probability ellipsoid | The ellipsoid is a plausibility device, not a probability of occurrence |
| Expected shortfall and VaR | Rockafellar and Uryasev (2000) | Layer 5.5 tail risk | Weighted historical VaR/ES with boundary atom | Historical simulation, not a parametric tail model |
| Minimum relative entropy | Jaynes (1957); Meucci (2008) | Layer 5.5 entropy pooling | One-constraint exponential tilt solved by bisection | Only one GDP constraint; no joint macro constraints |
| Effective sample size | Kish (1965) | Layer 5.5 ESS floor | \(1/\sum p_i^2\), blend back to prior | Treats weights as survey weights; not a full design effect |
| Optimal transport and Wasserstein medoids | Kantorovich (1942); Villani (2009) | Layer 5.5 reduction | Weighted PC1 scores plus greedy \(k\)-medoids | Greedy heuristic, not an exact Wasserstein barycenter |
| Stock-flow consistency | Godley and Lavoie (2007) | Layer 5 SFC projection | Reserve identity, Doran projection, absorbing floor | Three-series identity only; no full sectoral balance sheets |
| Balance-of-payments accounting | IMF BPM6 | Layer 5 reserve identity | \(\Delta\text{Res}=\text{CA}+\text{FA}\) with tolerance | Simplified two-account identity |
| Monetary policy corridor | Policy-rate corridor literature | Layer 5 active branch | Bills, FX intervention, OMO, interest-rate smoothing | Reduced-form rules, not a central-bank reaction function |
| Exchange-rate regime classification | Mundell-Fleming trilemma | Layer 5 archetypes | Floating, currency board, and managed corridor variants | Three stylized archetypes, not a continuous regime |
| Fiscal sustainability | Bohn (1998) | Layer 6 satellite | Debt snowball with primary balance and stock-flow adjustment | Reference coefficients, no estimated fiscal reaction function |
| Term-structure factors | Nelson and Siegel (1987); Diebold, Rudebusch, and Aruoba (2006) | Layer 6 satellite | Level, slope, curvature from factors | Coefficients are calibration values |
| Bank capital and credit supply | Adrian and Shin (2010) | Layer 6 satellite | CAR update from NIM and NPL changes, credit ceiling | Reduced-form, no bank balance-sheet optimization |
| Generalized forecast-error variance decomposition | Koop, Pesaran, and Potter (1996) | Layer 6 GFEVD | Observables-projected GFEVD, row-normalized | Linear projection, no sign restrictions |

### 7.2 Household theories

| Theory | Reference | Where used | What the code actually does | Main limitation |
|---|---|---|---|---|
| Consumer demand systems | Deaton and Muellbauer (1980) | QUAIDS block | Local share update around observed base shares | Not a full demand-system solution |
| Quadratic almost ideal demand system | Banks, Blundell, and Lewbel (1997) | QUAIDS block | Quadratic log-expenditure term with \(\lambda/b(p)\) | Coefficients are illustrative placeholders |
| Linear expenditure system / Stone–Geary | Stone (1954); Geary (1950) | Subsistence floor | \(E_{\text{sub}}=D_h\sum p_g\gamma_g\) | Subsistence quantities are fixed |
| Cost-of-living index theory | Konüs (1924); Törnqvist (1936); Fisher (1922) | Index block | Törnqvist, Fisher, Laspeyres, Paasche | Hicksian CV is first-order; no exact expenditure function |
| Equivalence scales | OECD modified scale; BLS SES | Equivalence block | Adult and child weights, food-price adjustment | Weights are calibration assumptions |
| Permanent income hypothesis | Friedman (1957) | BPP block | Permanent and transitory income components | Online path uses configured loadings, not estimated panels |
| Partial insurance | Blundell, Pistaferri, and Preston (2008) | BPP block | Consumption growth \(\psi v+\phi u\) | Pass-through coefficients are calibration values |
| Buffer-stock saving | Carroll (1997); Carroll and Toche (2009) | Carroll–Toche block | Target market-resources ratio, RIC/GIC | Target ratio is dimensionless, not months |
| Endogenous grid method | Carroll (2006) | EGM block | One-asset consumption function with five-node income shock | Compact solver, not a full permanent-income model |
| Heterogeneous-agent models with two assets | Kaplan, Moll, and Violante (2018) | HANK block | Liquid buffer and MDSR thresholds, regime-specific MPC | Threshold rule, not a solved HANK equilibrium |
| Merton portfolio choice | Merton (1969) | Merton share | \(\omega^*=erp/(\rho\sigma_r^2)\), clipped | Myopic mean-variance, no dynamic hedging |
| Poverty measurement | Foster, Greer, and Thorbecke (1984) | FGT block | P0 headcount, P1 gap, P2 severity | Poverty line is a configuration value |
| Vulnerability as expected poverty | Chaudhuri, Jalan, and Suryahadi (2002) | VEP block | Normal CDF of log consumption below the line | Known variance, no estimated consumption process |
| Precautionary saving | Leland (1968); Kimball (1990) | Carroll–Toche prudence term | \(\omega=(\rho-1)/2\) in the target formula | Reduced-form approximation |
| Life-cycle / permanent-income consumption | Modigliani and Brumberg (1954) | Conceptual background | Consumption smoothing logic | No explicit age profile |
| General-equilibrium incidence | — | Not implemented | Partial-equilibrium arithmetic only | No price or wage feedback from household behavior |

### 7.3 Risk, decision, and inference methods

| Method | Reference | Where used | Role |
|---|---|---|---|
| Coherent risk measure | Artzner et al. (1999) | ES calculation | Tail-risk reporting |
| Extreme-value peaks-over-threshold | Balkema–de Haan; Pickands | GPD marginals | Tail extrapolation |
| Gaussian copula | Sklar; Embrechts et al. | Plausibility ellipsoid | Dependence in standardized space |
| Student-t kernel | Multivariate t literature | Narrative weights | Heavy-tailed plausibility weighting |
| CMA-ES | Hansen and Ostermeier (2001) | Adversarial search | Derivative-free maximization |
| Sobol sequences and Owen scrambling | Sobol (1967); Owen (1995) | Layer 5 | Low-discrepancy common random numbers |
| Importance sampling / Radon–Nikodym | Geweke (1989) | Defensive mixture | Reweighting the tail stratum |
| Entropy pooling | Meucci (2008) | Layer 5.5 | Imposing a GDP view |
| \(k\)-medoids clustering | Kaufman and Rousseeuw (1990) | Layer 5.5 | Scenario reduction |
| Generalized variance decomposition | Koop–Pesaran–Potter | Layer 6 | Shock transmission summary |

### 7.4 Orchestration and computer-science methods

| Method | Reference or origin | Where used | Role |
|---|---|---|---|
| Probabilistic logic programming | ProbLog, De Raedt et al. (2007) | S6 | Hypothesis viability and conflict probability |
| Elo rating | Elo (1978) | S6 tournament | Pairwise hypothesis ranking |
| UCB1 bandit | Auer, Cesa-Bianchi, and Fischer (2002) | S6 model selection | Exploration–exploitation trade-off |
| Maximal marginal relevance | Carbonell and Goldstein (1998) | S6 retrieval | Diversity in selected documents |
| CRAG tri-state retrieval | Retrieval-augmented generation literature | S6 retrieval | Correct, ambiguous, or low-confidence retrieval |
| Contrastive ingestion | Contrastive representation learning | S6 ingestion | Filter promotional or unsourced text |
| HMAC-SHA256 | RFC 2104 | S6 sandbox | Execution receipt integrity |
| JSON canonicalization | RFC 8785 | Macro artifact | Stable hashing |
| Zero-copy Arrow IPC | Apache Arrow | Firm and population bridges | Avoid materializing large columns |

---

## 8. Privacy, security, and governance

### 8.1 Data classes

The system handles four classes of data:

1. **User query and optional personal context.** Free text, region, occupation
   sector, household notes, and asset/liability notes.
2. **Household profile.** Explicit numeric amounts: income, expenditure, budget
   shares, adults, children, liquid and illiquid assets, debt, debt service, FX
   assets and liabilities, and risk preferences.
3. **Processed article and evidence text.** Titles, truncated article bodies,
   summaries, and claim quotes.
4. **Macro data and artifacts.** The World Bank panel, macro artifact, firm
   table, cache-log entries, and run outputs.

### 8.2 Personal context handling

Personal context stays in memory during the run. `RawMaterial` does not carry
the `PersonalContext` object, so the cache log cannot persist it by accident.
S1 derives personal dimensions and explicit numeric parameters from the context
for the current run. When `persist_opt_in=false`:

- S3 copies the query breakdown and empties `personal_dimensions` and
  `explicit_parameters` before writing it;
- the artifact's `raw_material.json` removes the same two keys and adds
  `personal_parameters_redacted: true`;
- the LLM trace removes `user_prompt_excerpt`, `response_excerpt`, `parsed`,
  `validation_error`, and `transport_error`; for the S1 `query_breakdown` call
  it also removes the prompt hashes and adds `prompt_redacted: true`.

The user query itself is still written to the cache log, because it is the
request. A query can contain personal details. The pipeline does not attempt to
detect or redact personal names, amounts, or addresses inside the query text.
Users who need full query confidentiality should run with `dry_run=true` or
avoid putting identifying details in the query.

When `persist_opt_in=true`, the personal dimensions and parameters may be
written to the cache log and artifacts. The flag is opt-in and defaults to
false.

### 8.3 Cache lifecycle and purge

S3 writes processed content to `cache_log_entries` in SQLite. It does not write
raw article HTML. The cache follows the six-state lifecycle in Section 3.3.
On a successful S5, rows move to `verified` and then to `purged` when
`cache_log.purge_enabled=true`. On a failed check, rows move to `quarantined`
and are not deleted by the revalidation module.

`dry_run=true` uses an in-memory cache store, skips external writes, skips
artifact writes, and disables S6. It is the safest mode for testing with
sensitive queries. It still runs the numerical engines in memory.

Quarantined rows can be listed with `quarantine-list` and deleted with
`quarantine-delete --yes`. Deletion is explicit and not automatic.

### 8.4 LLM and embedding provider exposure

The default generation provider is local Ollama at
`http://127.0.0.1:11434`. The default embedding provider is `auto`: Cohere is
used when `COHERE_API_KEY` is set, otherwise local Ollama
`embeddinggemma-2:740m`.

Data leaves the machine when:

- Cohere embeddings are enabled: document and query text is sent to
  `https://api.cohere.com/v2/embed`;
- an OpenAI-compatible BYOK endpoint is configured: prompts and processed text
  are sent to that base URL;
- live article fetching is enabled: the pipeline makes outbound HTTP requests
  to the configured news sources and the World Bank API.

The World Bank panel fetch sends only the country code and indicator code, not
user text. Local Ollama keeps generation and embeddings on the machine.

For sensitive household data, use local Ollama and leave `COHERE_API_KEY` and
the BYOK key empty. The pipeline does not enforce this choice; it is an
operational decision.

### 8.5 Secrets

`redact_secrets()` removes:

- environment values whose variable name contains `API_KEY`, `APIKEY`,
  `ACCESS_TOKEN`, `AUTH_TOKEN`, `SECRET`, `PASSWORD`, `PASSCODE`, or
  `PRIVATE_KEY`, when the value is at least eight characters;
- values supplied explicitly through `extra_secrets`;
- patterns for `sk-...`, GitHub tokens, `Bearer ...`, `api_key=...`,
  `token=...`, `secret=...`, `password=...`, and `key=...`.

Redaction is applied to structured error messages and suggestions before they
are serialized. It is not a general-purpose data-loss-prevention system. A
secret that does not match one of these patterns and is not present in the
environment can still appear in a log.

The HTTP API never accepts an API key in the request body. BYOK is supplied to
the server process through environment variables, the gitignored `.env` file,
or the CLI's `--byok-stdin` flag, which reads one line from standard input and
does not echo it. API keys must not enter the cache, logs, or database.

### 8.6 Sandbox and attestation

The sandbox runs a Python script in a child process with:

- a dedicated directory under `/tmp/sandbox/{task_id}/{hypothesis_id}/`;
- a minimal environment containing only `PATH` and `PYTHONIOENCODING`;
- an 8.0-second timeout;
- no inherited API keys or database credentials.

The child process is a normal operating-system process. It is not a container,
virtual machine, seccomp jail, or network namespace. Code running in the
sandbox can read files accessible to the same user and can make network calls
unless the host restricts them. Treat the sandbox as a timeout and environment
isolation measure, not a security boundary against malicious code.

A result is attested only when the exit code is zero and every declared metric
lies inside its bound. The receipt is

\[
\text{receipt}=\operatorname{HMAC\text{-}SHA256}
\left(\text{secret},\;
\text{hypothesis\_id}:\text{universal\_parameter\_forcing}:0:\text{digest}\right),
\]

where \(\text{digest}=\operatorname{SHA256}(\text{stdout})_{:16}\). On success,
computed metrics are written as `verified_<metric> = 0.95`. Facts that carry a
`verified_*` predicate without a valid receipt are stripped before they reach
the final output.

The HMAC secret is `SOVEREIGN_SANDBOX_SECRET`. The code has a development
fallback string. A production deployment must set a strong secret; otherwise
anyone with the source can forge receipts.

### 8.7 Governance recommendations

For a deployment that handles real household data:

- Run with local Ollama and no Cohere key.
- Set `persist_opt_in=false` and prefer `dry_run=true` for exploratory queries.
- Keep the SQLite database, Chroma directory, `cache/`, and artifact
  directories outside version control.
- Set a strong `SOVEREIGN_SANDBOX_SECRET`.
- Restrict network access for the sandbox process at the host or container
  level.
- Rotate API keys and remove them from shell history and environment dumps.
- Treat quarantine rows as retained personal data and delete them under the
  same policy as the cache.
- Review the query text before logging it; the pipeline does not redact personal
  details inside the query.

---

## 9. Configuration reference

### 9.1 Files and precedence

| File | Purpose |
|---|---|
| `config/pipeline_v2.json` | Classifier weights, cache log, revalidation, input fetch, query breakdown, BYOK, orchestrator, macro and micro paths |
| `config/classifier_calibration.json` | Effective classifier threshold, mixed margin, and calibration metrics |
| `config/micro_household.json` | QUAIDS, LES, equivalence scales, price transmission, BPP, Carroll–Toche, EGM, HANK, FGT/VEP parameters |
| `config/micro_channels.json` | Channel list, formula handlers, direction dead bands |
| `.env` / environment | LLM provider, embedding provider, API keys, cache paths, macro defaults, UCB1 exploration constant |
| `app/core/config.py` | Environment schema and defaults |

The classifier reads the threshold and margin from
`classifier_calibration.json`, not from `pipeline_v2.json`. The latter also
declares `classifier.mixed_margin`, but the classifier uses the calibration
file. This is a deliberate separation between the operating point and the
calibration record, but it can confuse an operator who edits only one file.

Missing required files or fields raise `ConfigError`; the code does not fall
back to invented defaults for required model coefficients.

### 9.2 `config/pipeline_v2.json`

| Section | Key | Value | Meaning |
|---|---|---:|---|
| (top level) | `version` | `1.0` | Config schema version |
| | `artifact_dir` | `/tmp/osint_pipeline_v2` | Run artifacts |
| | `calibration_path` | `config/classifier_calibration.json` | Classifier operating point |
| `classifier` | `lexicon_weight` | 0.55 | Weight on the lexicon score |
| | `embedding_weight` | 0.45 | Weight on the embedding score |
| | `mixed_margin` | 0.08 | Declared margin; the classifier uses the calibration file's 0.02 |
| | `lexicon_saturation` | 5 | Hits needed for a full lexicon score |
| | `max_text_chars` | 6000 | Classification text cap |
| | `llm_assist_enabled` | false | Optional LLM assist |
| `cache_log` | `purge_enabled` | true | Purge after successful verification |
| | `dry_run_default` | false | Default dry-run behavior |
| | `store_embedding_vectors` | true | Store vectors in the cache |
| | `max_content_chars` | 20000 | Cache content cap |
| `revalidation` | `grounding_similarity_threshold` | 0.34 | Declared but not used by grounding |
| | `consistency_similarity_threshold` | 0.28 | Embedding cosine threshold |
| | `lexical_overlap_threshold` | 0.18 | Jaccard fallback threshold |
| | `min_claim_support_ratio` | 0.8 | Fraction of items that must pass |
| | `require_external_support` | true | Require at least one external citation |
| | `embedding_batch_size` | 16 | Revalidation embedding batch |
| | `append_failure_lessons` | false | Append failure lessons to negative memory |
| `input_fetch` | `require_live_article` | false | Fail instead of falling back |
| | `analyze_article` | true | Run article analysis |
| | `max_article_chars` | 6000 | Article body cap |
| | `fetch_attempts` | 2 | Live fetch retries |
| | `query_breakdown_enabled` | true | Deterministic breakdown |
| | `query_breakdown_llm_enabled` | true | Optional LLM topics |
| `orchestrator` | `enabled` | true | Run S6 |
| | `disable_raw_ingestors` | true | Disable raw ingestors during S6 |
| | `domain_tag` | `macro_econ` | Domain for ontology and memory |
| | `timeout_seconds` | 2700.0 | S6 timeout (45 minutes) |
| `byok` | `default_base_url` | `http://127.0.0.1:3001/v1` | OpenAI-compatible base URL |
| | `default_model` | `gemini-3.8-flash` | Default model name |
| | `timeout_seconds` | 180.0 | BYOK request timeout |
| `macro` | `artifact_subdir` | `macro` | Artifact subdirectory |
| | `country_code` | `VNM` | World Bank country |
| `micro` | `household_config_path` | `config/micro_household.json` | Household coefficients |
| | `channels_path` | `config/micro_channels.json` | Channel list |

### 9.3 `config/classifier_calibration.json`

| Key | Value |
|---|---:|
| `version` | 1.0 |
| `calibrated_at` | 2026-10-29T14:55:15+00:00 |
| `method` | `grid_search_accuracy_balanced_macro_f1` |
| `n_samples` | 46 |
| `threshold` | 0.51 |
| `mixed_margin` | 0.02 |
| `accuracy` | 1.0 |
| `balanced_accuracy` | 1.0 |
| `macro_f1` | 1.0 |
| Confusion matrix | 15 macro, 16 micro, 15 mixed, all on the diagonal |

The perfect metrics are on the calibration sample, not on production data. The
sample is small and was used to choose the threshold and margin, so the reported
accuracy is in-sample.

### 9.4 `config/micro_household.json`

| Key | Value | Role |
|---|---:|---|
| `quaids_alpha` | 0.42, 0.24, 0.12, 0.22 | QUAIDS intercept shares |
| `quaids_beta` | -0.160, -0.104, 0.029, 0.235 | Log-expenditure coefficients |
| `quaids_lambda` | -0.012, -0.008, 0.005, 0.015 | Quadratic expenditure coefficients |
| `quaids_gamma` | 4×4 symmetric matrix | Relative-price coefficients |
| `quaids_alpha_0` | 1.0 | Translog constant |
| `les_subsistence_quantities` | 1.80, 1.20, 0.45, 0.35 | Stone–Geary quantities |
| `equivalence_scales.oecd_modified` | adult 0.5, child 0.3 | OECD modified scale |
| `equivalence_scales.bls_ses` | adult 0.267, child 0.099 | BLS scale |
| `equivalence_scales.eta_child_food` | 0.08 | Food-price scale adjustment |
| `m_price_transmission` | 4×5 matrix | Macro factor to consumer price mapping |
| `price_projection_clip_ln` | -0.35, 0.65 | Log price clip |
| `vep_vulnerable_threshold` | 0.292 | Vulnerable VEP threshold |
| `vep_high_fragility_threshold` | 0.500 | High-fragility VEP threshold |
| `poverty_line_z_monthly_million_vnd` | 3.50 | Poverty line |
| `policy_dampener_per_unit` | -0.012 | Price effect per policy unit |
| `regime_markup` | 0.025 | Price effect of stress probability |
| `erp_base` | 0.055 | Equity risk premium base |
| `erp_factor_growth` | 0.012 | ERP loading on factor 4 |
| `erp_factor_liquidity` | -0.018 | ERP loading on factor 2 |
| `erp_bounds` | 0.01, 0.14 | ERP clip |
| `return_variance_base` | 0.032 | Return variance base |
| `return_variance_stress` | 0.015 | Variance effect of stress |
| `return_variance_global` | 0.008 | Variance effect of absolute factor-1 value |
| `return_variance_bounds` | 0.015, 0.12 | Variance clip |
| `sigma_c_base` | 0.14 | Log-consumption standard deviation |
| `sigma_c_stress` | 0.06 | Stress effect on log-consumption volatility |
| `carroll_toche_unemp_prob_base` | 0.03 | Base unemployment probability |
| `carroll_toche_unemp_prob_stress` | 0.04 | Stress effect on unemployment probability |
| `bpp_price_transitory_pass_through` | 0.35 | Price effect on transitory income |
| `default_budget_shares` | 0.45, 0.25, 0.12, 0.18 | Fallback shares |
| `default_crra_rho` | 2.5 | Default CRRA |
| `default_psi_perm_pass` | 0.78 | Default permanent pass-through |
| `default_phi_trans_pass` | 0.18 | Default transitory pass-through |
| `default_beta_perm_macro` | -0.015, -0.010, -0.012, 0.045, -0.018 | Permanent loadings |
| `default_beta_trans_macro` | -0.020, -0.035, -0.025, 0.030, -0.030 | Transitory loadings |
| `mpc_by_regime` | 0.055, 0.28, 0.52 | MPC by HANK regime |
| `liquid_buffer_threshold_months` | 3.0 | Liquid buffer threshold |
| `mdsr_threshold` | 0.30 | Debt-service threshold |
| `egm_n_grid` | 80 | Asset grid points |
| `egm_a_max` | 24.0 | Maximum assets |
| `egm_rho` | 2.5 | EGM CRRA |
| `egm_R_gross` | 1.01 | Gross return |
| `egm_beta_disc` | 0.985 | Discount factor |
| `egm_gamma_growth` | 1.005 | Income growth |
| `egm_sigma_xi` | 0.15 | Income shock volatility |
| `egm_borrow_limit` | 0.0 | Borrowing limit |
| `egm_max_iter` | 120 | EGM iterations |
| `egm_tol` | 1e-6 | EGM tolerance |

The file's `source_note` is part of the configuration and states the
recalibration requirement. It is injected into the household assumptions.

### 9.5 `config/micro_channels.json`

| Channel id | Formula handler | Enabled | Direction epsilon |
|---|---|---:|---:|
| `prices` | `tornqvist_coli` | true | 0.0001 |
| `income_employment` | `bpp_income` | true | 0.01 |
| `borrowing_cost` | `debt_service_rate` | true | 0.005 |
| `savings_return` | `savings_real_return` | true | 0.0005 |
| `exchange_rate` | `fx_exposure` | true | 0.005 |
| `assets_liabilities` | `balance_sheet` | true | 0.0001 |
| `public_transfers` | `policy_support` | true | 0.0001 |

`direction_epsilon` is the generic fallback. `channel_direction_epsilons`
overrides it for the channels listed. An unknown formula handler produces a
warning and skips that channel; it does not crash the run.

### 9.6 Environment settings

| Setting | Default | Meaning |
|---|---:|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./osint_macro.db` | SQLite database |
| `LLM_PROVIDER` | `local_ollama` | Generation provider |
| `LOCAL_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `LOCAL_MODEL_NAME` | `llama3.2:3b` | Local generation model |
| `LOCAL_LLM_CONCURRENCY` | 1 | Concurrent local requests |
| `LOCAL_LLM_NUM_CTX` | 4096 | Context window |
| `LOCAL_LLM_TIMEOUT_SECONDS` | 600 | Local request timeout |
| `EMBEDDING_PROVIDER` | `auto` | `cohere`, `embeddinggemma`, or auto |
| `EMBEDDING_MODEL` | `embeddinggemma-2:740m` | Local embedding model |
| `EMBEDDING_DIMENSION` | 256 | Embedding dimension |
| `COHERE_EMBED_MODEL` | `embed-v5.0-pro` | Cohere embedding model |
| `INGESTION_CONTRASTIVE_THRESHOLD` | 0.42 | Contrastive acceptance |
| `CONTRASTIVE_NEGATIVE_MARGIN` | 0.30 | Negative-anchor margin |
| `CONTRASTIVE_NEGATIVE_WEIGHT` | 0.50 | Negative-anchor penalty |
| `CRAG_CORRECT_THRESHOLD` | 0.62 | CRAG correct state |
| `CRAG_AMBIGUOUS_THRESHOLD` | 0.40 | CRAG ambiguous state |
| `MMR_BETA` | 0.70 | MMR relevance weight |
| `MMR_TIME_DECAY_GAMMA` | 0.005 | Time decay per day |
| `DECOMPOSE_KEEP_THRESHOLD` | 0.48 | Decompose–recompose sentence threshold |
| `NUMERICAL_CV_THRESHOLD` | 0.35 | Numerical divergence threshold |
| `PROXIMITY_SIMILARITY_THRESHOLD` | 0.86 | Mode-collapse proximity |
| `NEGATIVE_MEMORY_DECAY_FACTOR` | 0.60 | Negative-memory decay |
| `UCB1_EXPLORATION_C` | 1.0 | UCB1 exploration constant |
| `SOVEREIGN_SANDBOX_SECRET` | development fallback | HMAC receipt secret |
| `MACRO_STRESS_PANEL_CACHE_DIR` | `./cache/world_bank_panel` | Panel cache |
| `MACRO_STRESS_CACHE_TTL_HOURS` | 168 | Panel cache TTL |
| `MACRO_STRESS_N_PARTICLES` | 8192 | Simulation particles |
| `MACRO_STRESS_K_REP` | 50 | Wasserstein medoids |
| `MACRO_STRESS_M_CAND` | 250 | Medoid candidates |
| `MACRO_STRESS_CMA_POPULATION` | 32 | CMA-ES population |
| `MACRO_STRESS_CMA_GENERATIONS` | 50 | CMA-ES generations |
| `MACRO_STRESS_HORIZON` | 4 | Simulation horizon |
| `MACRO_STRESS_ENABLE_MICRO` | true | Firm satellite |
| `MACRO_STRESS_FIRM_COUNT` | 1600 | Synthetic firm count |
| `MACRO_STRESS_FIRM_DATA_PATH` | empty | Optional real Arrow IPC table |
| `MACRO_STRESS_ENABLE_LEGACY_ARIMA_THEORY` | false | Legacy ARIMA plugin |

### 9.7 Validation rules and defaults

Configuration is validated with Pydantic:

- `quaids_gamma` must be 4×4;
- `quaids_alpha`, `quaids_beta`, `quaids_lambda`, and
  `les_subsistence_quantities` must have four entries;
- `m_price_transmission` must be 4×5;
- `mpc_by_regime` must have three entries;
- `price_projection_clip_ln` must be `[lower, upper]` with lower < upper;
- `equivalence_scales` must contain `oecd_modified`, `bls_ses`, and
  `eta_child_food`;
- channel ids must be unique and the channel list must not be empty;
- `direction_epsilon` must be in `(0, 0.5)`;
- household `budget_shares_w0` must have four entries in `[0,1]` summing to 1;
- `n_adults` is an integer in `[1,20]` and `n_children` in `[0,20]`;
- monetary amounts are non-negative, and income and expenditure must be
  positive when present;
- `crra_rho` is in `(0,20]`;
- pass-throughs and risk premia are in `[0,1]`;
- `beta_perm_macro` and `beta_trans_macro` must have five entries.

The QUAIDS restrictions are checked again in
`CostOfLivingAndEquivalenceEngine.__init__`, so a configuration that passes the
Pydantic shape checks but violates adding-up, homogeneity, or symmetry fails at
engine construction.

### 9.8 Values that are not configurable

Some reference values are embedded in code:

- the 36 series codes and World Bank source codes in `PANEL_SERIES`;
- the panel years 1996–2025;
- the initial transition matrix `[[0.94,0.06],[0.18,0.82]]`;
- the adverse-target and policy-target matrices in `calibration.py`;
- the shock decay vector in `calibration.py`;
- the GPD threshold quantile 0.90;
- the eigenvalue floor 0.05 in the copula projection;
- the Haar bank size 2×64;
- the simulation twist vector;
- the gatekeeper thresholds 5.0 billion USD reserves, NPL `[0.2,35]`,
  interbank `[0.1,40]`, and NPL breach 3.5;
- the entropy-pooling bisection range and iteration count;
- the Kish ESS floor 200;
- the Wasserstein time discount 0.95;
- the firm stress thresholds 0.80 and 1.25;
- the S6 ProbLog status thresholds 0.65 and 0.20;
- the S6 conflict-trace threshold 0.25;
- the sandbox timeout 8.0 seconds;
- the S6 negative-memory base weight 0.99.

Changing any of these requires a code change and, for the macro values, a new
artifact hash and regression run.

---

## 10. Operating the system

### 10.1 Prerequisites

- Python 3.11 or newer.
- A virtual environment at `.venv`.
- Local Ollama if the default LLM or embedding path is used.
- Network access for the World Bank panel and, optionally, live articles and
  Cohere.
- Enough RAM for the macro engine. The full 8,192-particle simulation, the
  36×36 GFEVD, and the Arrow tables fit in a few gigabytes; the reference host
  has 7.6 GB and runs one local model at a time.

### 10.2 Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env: set the LLM provider, embedding provider, and any keys.
.venv/bin/python init_db.py
```

Ollama models used by the reference environment:

```bash
ollama pull llama3.2:3b
ollama pull embeddinggemma-2:740m
```

If Cohere is enabled, set `COHERE_API_KEY`. If a BYOK OpenAI-compatible
endpoint is used, set `OSINT_BYOK_BASE_URL` and `OSINT_BYOK_MODEL`, and supply
the key through `--byok-stdin`, the process environment, or the gitignored
`.env` file. The BYOK router loads `.env` without overriding variables that are
already set in the process environment.

### 10.3 CLI

The CLI entry point is `run_full_workflow.py` or
`python -m app.pipeline_v2.cli`:

```bash
# Safe smoke test: no database write, no artifact, no S6.
.venv/bin/python run_full_workflow.py --mode micro \
  --query "Giá thực phẩm tăng ảnh hưởng chi tiêu ra sao?" \
  --dry-run --skip-orchestrator

# Macro run with the full S1-S6 path.
.venv/bin/python run_full_workflow.py --mode macro \
  --query "Lạm phát và tỷ giá tác động thế nào?" \
  --output human

# Dual run with an explicit household profile.
.venv/bin/python run_full_workflow.py --mode dual \
  --query "Chi phí sinh hoạt và nợ của hộ gia đình" \
  --household-file household.json \
  --output json

# Auto mode that may fall back to dual.
.venv/bin/python run_full_workflow.py --mode auto \
  --query "Lạm phát ảnh hưởng hộ gia đình tôi thế nào?" \
  --allow-dual

# Live article fetch.
.venv/bin/python run_full_workflow.py --mode macro \
  --query "Điều chỉnh lãi suất" --live

# BYOK key from stdin, never echoed.
printf '%s\n' "$OSINT_BYOK_API_KEY" | \
  .venv/bin/python run_full_workflow.py --mode macro \
  --query "..." --byok-stdin
```

`--dry-run` skips the database, artifact writes, purge, and S6. It still runs
S1–S5 in memory. `--skip-orchestrator` skips only S6 and keeps the normal cache
and artifact behavior. `--allow-dual` lets `auto` resolve a mixed or
low-confidence classification to `dual`; without it the run raises
`MODE_SELECTION_REQUIRED`.

Quarantine commands:

```bash
.venv/bin/python run_full_workflow.py quarantine-list
.venv/bin/python run_full_workflow.py quarantine-delete --run-id <run_id> --yes
```

### 10.4 HTTP API

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Endpoint: `POST /api/v1/pipeline-v2/run`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/pipeline-v2/run \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Lạm phát ảnh hưởng hộ gia đình tôi thế nào?",
    "mode": "auto",
    "dry_run": true
  }'
```

Request fields:

| Field | Type | Default |
|---|---|---|
| `query` | string, non-empty | required |
| `mode` | `macro`, `micro`, `dual`, `auto` | `auto` |
| `personal_context` | object or null | null |
| `household_profile` | object or null | null |
| `allow_dual` | boolean | false |
| `dry_run` | boolean | false |
| `use_live` | boolean | false |
| `enable_orchestrator` | boolean | true |
| `model` | string or null | null |

The endpoint never accepts an API key in the body. HTTP status mapping:
exit code 2 → 400, 3 → 502, 4 → 500, 5 → 422, 6 → 409, 1 → 500.

### 10.5 Tests and stress runs

```bash
.venv/bin/python tests/test_pipeline_v2.py
.venv/bin/python tests/test_micro_household_subsystem.py
.venv/bin/python tests/test_macro_stress_v57.py
```

The macro test uses live World Bank data only when `--live` is passed. The
pipeline test uses an intentionally failing embedding client to exercise the
lexicon fallback without calling a real LLM or network.

Ten-scenario stress matrix:

```bash
.venv/bin/python scripts/stress_pipeline_v2.py
# Skip the slow full S6 run:
.venv/bin/python scripts/stress_pipeline_v2.py --skip-full-s6
```

The full S6 run with local Ollama takes about 20 minutes on the reference host.
The configured timeout is 2,700 seconds.

### 10.6 Artifacts and directories

| Path | Content |
|---|---|
| `/tmp/osint_pipeline_v2/` | Default pipeline run artifacts |
| `macro_stress_artifact.json` | Signed macro artifact |
| `raw_material.json` | Redacted raw material |
| `llm_calls.json` | Redacted LLM trace |
| `orchestration_output.json` | S6 output, when S6 completes |
| `/tmp/sandbox/{task}/{hypothesis}/` | Sandbox scripts and outputs |
| `./cache/world_bank_panel/` | World Bank indicator cache |
| `./cache/llm_responses/` | LLM response cache |
| `./data/chroma_db/` | Chroma vector store |
| `./osint_macro.db` | SQLite database |
| `./firms_stress_downscaled_v5_7.arrow` | Firm stress output under the macro artifact directory |

Artifacts are written outside the repository by default. The macro engine also
writes `firms_vietnam_zero_copy.arrow` when it creates the synthetic fixture.
The exact artifact directory is recorded in `PipelineOutput.artifacts`.

### 10.7 Performance and memory

Approximate reference timings on the 7.6 GB host:

| Stage | Time | Notes |
|---|---|---|
| World Bank panel fetch | seconds to minutes | 35 indicator requests, cached for 168 hours |
| Macro calibration and simulation | tens of seconds to a few minutes | 8,192 particles, CMA-ES 32×50, GFEVD 36×36 |
| Household single evaluation | milliseconds | 50 scenarios, vectorized NumPy |
| S6 orchestrator | about 20 minutes | Multi-stage local LLM chain, roughly 25–30 calls in a typical run |

The macro engine writes an Arrow firm table with 1,600 rows by default. The
population bridge is vectorized but materializes the output stress matrix of
size \(N_{\text{firms}}\times K_{\text{rep}}\); a very large firm table will
increase memory proportionally.

Local LLM concurrency is 1 by default to avoid loading the generation and
embedding models at the same time on a 7.6 GB machine. Increasing concurrency
without checking RAM can trigger the OOM killer.

### 10.8 Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `CONFIG_INVALID` | Missing or malformed config field | Read the message; check the JSON shape rules in Section 9.7 |
| `MODE_SELECTION_REQUIRED` | `auto` classified as mixed or low confidence | Pass `--mode macro`, `--mode micro`, or `--mode dual`; or add `--allow-dual` |
| `FETCH_FAILED` | Live article fetch failed and `require_live_article=true` | Retry, change sources, or set `require_live_article=false` |
| `SOURCE_UNAVAILABLE` | World Bank panel incomplete and no usable cache | Check network and `MACRO_STRESS_PANEL_CACHE_DIR`; retry later |
| `MACRO_FAILED` | Macro engine returned `FAILED` | Inspect the artifact `error` field and the panel's missing-cell count |
| `MICRO_FAILED` | Household bridge raised | Check the household profile types and the macro payload shape |
| `REVALIDATION_FAILED` | S5 check failed | Inspect the validation report; quarantine is expected |
| `PURGE_FAILED` | SQLite purge error | Check database permissions and disk space |
| S6 timeout | Local LLM slower than 2,700 seconds | Run with `--skip-orchestrator`, reduce the workload, or increase the timeout |
| Empty or missing macro panel | All observations missing for a column | The code refuses to synthesize the column; fix the source or cache |
| Household channel `not_estimated` | Missing required input | Provide the field; do not expect a numeric magnitude |
| Classifier behaves unexpectedly | Editing `pipeline_v2.json` margin instead of the calibration file | Edit `classifier_calibration.json` or recalibrate |

### 10.9 Reproducibility

- The macro engine uses the request seed (default `20261026`) for PCA/VAR
  calibration, the Sobol sampler, CMA-ES, and the Wasserstein reduction.
- The same panel, seed, and request produce the same deterministic stages.
- S6 is not deterministic. LLM sampling, retries, and judge ordering can change
  its output.
- The artifact hash covers the macro payload, so any change in the panel,
  request, or engine output changes `artifact_sha256`.
- `dry_run=true` avoids persistent state, which makes it suitable for repeated
  tests.
- The classifier calibration is in-sample; do not treat its perfect metrics as
  a production guarantee.

## 11. Appendices

### A.1 Notation

| Symbol | Meaning | Unit or range |
|---|---|---|
| \(x\) | Nominal monthly household expenditure | million VND |
| \(y_0\) | Nominal monthly household income | million VND |
| \(p_g\) | Price of consumption good \(g\) | index, base 1 |
| \(w_g\) | Budget share of good \(g\) | share, sum to 1 |
| \(D_h\) | Household equivalence scale | equivalent adults |
| \(n_a,n_c\) | Number of adults, children | people |
| \(\rho\) | CRRA risk-aversion coefficient | dimensionless, \(>0\) |
| \(R\) | Gross return | \(>1\) |
| \(\beta\) | Discount factor | \((0,1)\) |
| \(\Gamma\) | Income growth factor | \(>0\) |
| \(\sigma_u^2\) | Transitory income shock variance | log variance |
| \(\sigma_v^2\) | Permanent income shock variance | log variance |
| \(\psi,\phi\) | Permanent, transitory consumption pass-through | \([0,1]\) |
| \(F\) | Macro factor vector or matrix | standardized |
| \(\Lambda\) | Factor loadings | standardized |
| \(A_\ell\) | VAR coefficient at lag \(\ell\) | dimensionless |
| \(\mathcal{A}\) | Companion matrix | dimensionless |
| \(\Omega_s\) | Regime-\(s\) factor covariance | variance |
| \(P\) | Regime transition matrix | probability |
| \(R_c\) | Copula correlation matrix | correlation |
| \(\kappa\) | Mahalanobis radius | dimensionless |
| \(B_\varepsilon\) | Shock transmission matrix | factor units per shock SD |
| \(B_u\) | Policy transmission matrix | factor units per policy unit |
| \(\rho_j\) | Shock decay for dimension \(j\) | \((0,1)\) |
| \(N_p\) | Number of simulation particles | count |
| \(H\) | Simulation horizon | years |
| \(K_{\text{rep}}\) | Number of Wasserstein medoids | count |
| \(q_k\) | Medoid cluster weight | share, sum to 1 |
| \(z\) | Standardized shock vector | standard normal |
| \(\mu_{\text{twist}}\) | Tail-stratum mean shift | standardized |
| \(\pi_s\) | Regime stress probability | \([0,1]\) |
| \(u\) | Policy vector (bills, FX intervention, OMO) | mixed |
| \(E_{\text{sub}}\) | Subsistence expenditure floor | million VND/month |
| \(VEP\) | Vulnerability as expected poverty | probability |
| \(P_0,P_1,P_2\) | FGT headcount, gap, severity | index |
| \(GFEVD_{ij}\) | Share of target \(i\)'s variance from source \(j\) | percent |
| \(\mathrm{ESS}\) | Kish effective sample size | count |
| \(\mathrm{VaR}_\alpha\) | Value at risk at level \(\alpha\) | series unit |
| \(\mathrm{ES}_\alpha\) | Expected shortfall at level \(\alpha\) | series unit |

### A.2 The 36-series panel

| # | Code | World Bank indicator | Transform | Unit |
|---:|---|---|---|---|
| 0 | `GDP_YOY` | `NY.GDP.MKTP.KD.ZG` | direct | % |
| 1 | `CPI_YOY` | `FP.CPI.TOTL.ZG` | direct | % |
| 2 | `FX_DEPREC` | `PA.NUS.FCRF` | pct_change | % |
| 3 | `IB_RATE` | `FR.INR.LEND` | direct | % |
| 4 | `FX_RESERVES` | `FI.RES.TOTL.CD` | divide_1e9 | billion USD |
| 5 | `NPL_RATIO` | `FB.AST.NPER.ZS` | direct | % |
| 6 | `CREDIT_GROWTH` | `FS.AST.PRVT.GD.ZS` | pct_change | % |
| 7 | `CURRENT_ACCOUNT` | `BN.CAB.XOKA.CD` | divide_1e9 | billion USD |
| 8 | `FINANCIAL_ACCOUNT` | `BN.FIN.TOTL.CD` | divide_1e9 | billion USD |
| 9 | `GDP_PCAP_GROWTH` | `NY.GDP.PCAP.KD.ZG` | direct | % |
| 10 | `GDP_DEFLATOR` | `NY.GDP.DEFL.KD.ZG` | direct | % |
| 11 | `INVESTMENT_GDP` | `NE.GDI.TOTL.ZS` | direct | % GDP |
| 12 | `HOUSEHOLD_CONSUMPTION_GDP` | `NE.CON.PRVT.ZS` | direct | % GDP |
| 13 | `GOV_CONSUMPTION_GDP` | `NE.CON.GOVT.ZS` | direct | % GDP |
| 14 | `EXPORTS_GDP` | `NE.EXP.GNFS.ZS` | direct | % GDP |
| 15 | `IMPORTS_GDP` | `NE.IMP.GNFS.ZS` | direct | % GDP |
| 16 | `INDUSTRY_VA_GDP` | `NV.IND.TOTL.ZS` | direct | % GDP |
| 17 | `MANUFACTURING_VA_GDP` | `NV.IND.MANF.ZS` | direct | % GDP |
| 18 | `SERVICES_VA_GDP` | `NV.SRV.TOTL.ZS` | direct | % GDP |
| 19 | `AGRICULTURE_VA_GDP` | `NV.AGR.TOTL.ZS` | direct | % GDP |
| 20 | `POPULATION_GROWTH` | `SP.POP.GROW` | direct | % |
| 21 | `URBAN_POP_SHARE` | `SP.URB.TOTL.IN.ZS` | direct | % |
| 22 | `LABOR_FORCE_PARTICIPATION` | `SL.TLF.CACT.ZS` | direct | % |
| 23 | `UNEMPLOYMENT` | `SL.UEM.TOTL.ZS` | direct | % |
| 24 | `PRIMARY_ENROLLMENT` | `SE.PRM.ENRR` | direct | % |
| 25 | `HEALTH_EXPENDITURE_GDP` | `SH.XPD.CHEX.GD.ZS` | direct | % GDP |
| 26 | `LIFE_EXPECTANCY` | `SP.DYN.LE00.IN` | direct | years |
| 27 | `REAL_INTEREST_RATE` | `FR.INR.RINR` | direct | % |
| 28 | `BROAD_MONEY_GDP` | `FM.LBL.BMNY.GD.ZS` | direct | % GDP |
| 29 | `ODA_GDP` | `DT.ODA.ODAT.GN.ZS` | direct | % GNI |
| 30 | `FDI_GDP` | `BX.KLT.DINV.WD.GD.ZS` | direct | % GDP |
| 31 | `TERMS_OF_TRADE` | `TT.PRI.MRCH.XD.WD` | direct | index |
| 32 | `RESERVE_MONTHS` | `FI.RES.TOTL.MO` | direct | months |
| 33 | `FDI_USD` | `BX.KLT.DINV.CD.WD` | divide_1e9 | billion USD |
| 34 | `GDP_CURRENT_USD` | `NY.GDP.MKTP.CD` | divide_1e9 | billion USD |
| 35 | `EXCHANGE_RATE` | `PA.NUS.FCRF` | direct | VND/USD |

The first nine columns are the core contract. Columns 9–35 add context and are
used by the factor model and the GFEVD, but not by the branch simulation's
core calendar.

### A.3 Default parameters at a glance

**Macro**

| Parameter | Value |
|---|---:|
| Panel years | 1996–2025 |
| Panel columns | 36 |
| Core columns | 9 |
| PCA factors | 5 |
| VAR lags | 5 |
| Companion dimension | 25 |
| Regimes | 2 |
| Haar draws per regime | 64 |
| Simulation particles | 8,192 |
| Center stratum | 4,096 |
| Tail stratum | 4,096 |
| Horizon | 4 years |
| GPD threshold quantile | 0.90 |
| Copula tail level \(\alpha\) | 1e-4 |
| CMA-ES population | 32 |
| CMA-ES generations | 50 |
| Elite shocks | 16 |
| Wasserstein medoids | 50 |
| Medoid candidates | 250 |
| Time discount | 0.95 per year |
| VaR/ES level | 0.99 |
| Reserve floor | 10.0 billion USD |
| SFC tolerance | 5.0 billion USD (gate); 4.5 (projection) |
| NPL breach threshold | 3.5% |
| Kish ESS floor | min(200, 0.95N) |
| Firm count | 1,600 |
| Firm DVI critical threshold | 0.80 |
| Firm ICR critical threshold | 1.25 |
| Synthetic firm seed | 20261026 |

**Household**

| Parameter | Value |
|---|---:|
| Goods | 4 |
| Base shares | 0.45, 0.25, 0.12, 0.18 |
| Default CRRA | 2.5 |
| Poverty line | 3.50 million VND/month |
| Liquid buffer threshold | 3.0 months |
| MDSR threshold | 0.30 |
| MPC by regime | 0.055, 0.28, 0.52 |
| VEP vulnerable threshold | 0.292 |
| VEP high-fragility threshold | 0.500 |
| Price log clip | -0.35, 0.65 |
| Policy dampener per unit | -0.012 |
| Regime markup | 0.025 |
| BPP price pass-through | 0.35 |
| EGM grid | 80 points |
| EGM max assets | 24.0 |
| EGM return | 1.01 |
| EGM discount | 0.985 |
| EGM growth | 1.005 |
| EGM income shock SD | 0.15 |
| EGM borrowing limit | 0.0 |
| EGM tolerance | 1e-6 |

**Pipeline**

| Parameter | Value |
|---|---:|
| Classifier lexicon weight | 0.55 |
| Classifier embedding weight | 0.45 |
| Effective threshold | 0.51 |
| Effective mixed margin | 0.02 |
| Lexicon saturation | 5 |
| Cache content cap | 20,000 chars |
| Article body cap | 6,000 chars |
| Consistency cosine threshold | 0.28 |
| Lexical Jaccard threshold | 0.18 |
| Minimum support ratio | 0.8 |
| S6 timeout | 2,700 s |
| Sandbox timeout | 8.0 s |
| Panel cache TTL | 168 h |

### A.4 Formula index

| Formula | Section | Implementation |
|---|---|---|
| Panel transforms | 5.2 | `app/services/macro_stress.py` |
| Linear interpolation and median imputation | 5.3.1 | `calibration._impute_and_standardize` |
| PCA via SVD | 5.3.1 | `calibration.calibrate_panel` |
| Ridge VAR(5) and companion form | 5.3.2 | `calibration._fit_var`, `_companion` |
| Spectral-radius shrink | 5.3.2 | `calibration.calibrate_panel` |
| Two-regime covariance EM | 5.3.3 | `calibration._regime_covariances` |
| GPD CDF and quantile | 5.3.4 | `calibration.GpdMarginal` |
| Kendall-tau copula correlation | 5.3.5 | `calibration._copula_correlation` |
| Haar rotation bank | 5.3.6 | `calibration._haar_bank` |
| Shock and policy matrices | 5.3.7 | `calibration.calibrate_panel` |
| RBPF predict, update, Joseph form | 5.4 | `rbpf.run_rbpf_rts` |
| RTS backward pass | 5.4 | `rbpf.run_rbpf_rts` |
| Mahalanobis projection | 5.6.1 | `adversarial.project_to_ellipsoid` |
| Ellipsoid radius | 5.6.1 | `adversarial.kappa_squared` |
| Adversarial objective | 5.6.2 | `adversarial._objective_metrics` |
| CMA-ES update | 5.6.3 | `adversarial._cma_es` |
| Narrative weights | 5.6.4 | `adversarial._narrative_weights` |
| Sobol and defensive mixture | 5.7.1 | `simulation.defensive_mixture_shocks` |
| Shock allocation | 5.7.2 | `simulation.allocate_shock_blocks` |
| Active policy rule | 5.7.4 | `simulation._policy_step` |
| Doran SFC projection | 5.7.5 | `simulation._doran_projection` |
| Joint gate | 5.8.1 | `gatekeeper.evaluate_joint_gate` |
| Entropy pooling | 5.8.2 | `gatekeeper.entropy_pool_weights` |
| Kish ESS floor | 5.8.3 | `gatekeeper.enforce_kish_floor` |
| Weighted VaR/ES | 5.8.4 | `gatekeeper.weighted_var_es` |
| Branch metrics | 5.8.5 | `gatekeeper.branch_metrics` |
| Wasserstein medoids | 5.8.6 | `gatekeeper.wasserstein_reduction` |
| GFEVD | 5.9.1 | `gfevd.compute_gfevd` |
| Nelson–Siegel | 5.9.2 | `satellites.evaluate_structural_satellites` |
| Bohn debt snowball | 5.9.2 | `satellites.evaluate_structural_satellites` |
| Adrian–Shin CAR | 5.9.2 | `satellites.evaluate_structural_satellites` |
| Firm stress | 5.9.3 | `micro.decode_macro_archetypes_to_all_firms_zero_copy` |
| Price projection | 6.2 | `bridge_eco_engine._project_sectoral_prices_from_macro_medoids` |
| QUAIDS share update | 6.3 | `coli_quaids_equivalence.evaluate_quaids_and_superlative_indices` |
| Equivalence scales | 6.4 | `coli_quaids_equivalence.compute_equivalence_scale` |
| Törnqvist, Laspeyres, Paasche, Fisher | 6.5 | `coli_quaids_equivalence.evaluate_quaids_and_superlative_indices` |
| Hicksian CV | 6.5 | `coli_quaids_equivalence.evaluate_quaids_and_superlative_indices` |
| BPP covariance identification | 6.6.1 | `bpp_income_insurance.estimate_bpp_gmm_variances` |
| BPP projection | 6.6.2 | `bpp_income_insurance.project_conditional_income_and_consumption_shocks` |
| Carroll–Toche target | 6.7 | `buffer_stock_egm_hank_vep.compute_carroll_toche_target_buffer` |
| EGM | 6.8 | `buffer_stock_egm_hank_vep.solve_egm_policy_function` |
| HANK and Merton | 6.9 | `buffer_stock_egm_hank_vep.classify_two_asset_hank_and_merton_share` |
| FGT and VEP | 6.10 | `buffer_stock_egm_hank_vep.compute_fgt_and_3step_fgls_vep` |
| Seven channels | 6.11 | `app/pipeline_v2/micro_engine.py` |
| Contrastive ingestion | 4.6.1 | `embedding_engine._contrastive_score` |
| Temporal MMR | 4.6.2 | `embedding_engine.semantic_search` |
| CRAG tri-state | 4.6.2 | `embedding_engine.semantic_search` |
| ProbLog net score | 4.6.3 | `app/logic_engine/problog_scanner.py` |
| Negative memory | 4.6.4 | `app/memory/negative_memory_compiler.py` |
| UCB1 | 4.6.5 | `app/orchestrator/tournament.py` |
| Elo and hybrid fitness | 4.6.6 | `app/orchestrator/tournament.py` |
| HMAC attestation | 4.6.7 | `app/sandbox/attestation.py` |

### A.5 File map

| File | Role |
|---|---|
| `app/pipeline_v2/contracts.py` | Public and internal Pydantic contracts, cache states, household parser |
| `app/pipeline_v2/config.py` | JSON config loaders and validation |
| `app/pipeline_v2/input_fetch.py` | S1 deterministic and live input material |
| `app/pipeline_v2/classifier.py` | S2 lexicon and embedding classifier |
| `app/pipeline_v2/cache_log.py` | S3 SQLite cache log |
| `app/pipeline_v2/mode_router.py` | S4 mode resolution |
| `app/pipeline_v2/macro_adapter.py` | Macro request, run, and result mapping |
| `app/pipeline_v2/micro_engine.py` | S4b seven-channel household engine |
| `app/pipeline_v2/revalidation.py` | S5 checks and purge |
| `app/pipeline_v2/orchestrator.py` | Stage sequencing, artifacts, redaction, S6 |
| `app/pipeline_v2/cli.py` | Command-line interface |
| `app/pipeline_v2/errors.py` | Structured errors, exit codes, secret redaction |
| `app/api/v1/pipeline_v2.py` | HTTP endpoint |
| `app/math_engine/macro_stress_v57/calibration.py` | Layer 0 estimation |
| `app/math_engine/macro_stress_v57/rbpf.py` | Layer 1 filter and smoother |
| `app/math_engine/macro_stress_v57/regimes.py` | Layer 1.5 regime report |
| `app/math_engine/macro_stress_v57/adversarial.py` | Layers 2/3 shock search |
| `app/math_engine/macro_stress_v57/simulation.py` | Layer 5 simulation |
| `app/math_engine/macro_stress_v57/gatekeeper.py` | Layer 5.5 gate and reduction |
| `app/math_engine/macro_stress_v57/gfevd.py` | Layer 6 GFEVD and briefing |
| `app/math_engine/macro_stress_v57/satellites.py` | Layer 6 structural satellites |
| `app/math_engine/macro_stress_v57/micro.py` | Layer 6 firm downscaling |
| `app/math_engine/macro_stress_v57/engine.py` | Top-level orchestration and artifact |
| `app/math_engine/macro_stress_v57/markov.py` | Offline endogenous Markov routines; not called by the online simulation |
| `app/math_engine/micro_household/coli_quaids_equivalence.py` | QUAIDS, indices, equivalence, CV |
| `app/math_engine/micro_household/bpp_income_insurance.py` | BPP identification and projection |
| `app/math_engine/micro_household/buffer_stock_egm_hank_vep.py` | Carroll–Toche, EGM, HANK, Merton, FGT, VEP |
| `app/math_engine/micro_household/bridge_eco_engine.py` | Macro-to-micro bridge and population evaluation |
| `app/services/macro_stress.py` | World Bank panel and workflow adapter |
| `app/services/embedding_engine.py` | Embeddings, contrastive ingestion, MMR, CRAG |
| `app/services/word_filter.py` | Live article filter |
| `app/orchestrator/tournament.py` | Elo, UCB1, hybrid fitness |
| `app/orchestrator/universal_pipeline.py` | S6 pipeline |
| `app/logic_engine/problog_scanner.py` | ProbLog viability and conflict |
| `app/memory/negative_memory_compiler.py` | Negative-memory compilation |
| `app/sandbox/runner.py` | Sandbox process execution |
| `app/sandbox/attestation.py` | HMAC receipts and fact sanitization |
| `config/pipeline_v2.json` | Pipeline configuration |
| `config/classifier_calibration.json` | Classifier operating point |
| `config/micro_household.json` | Household coefficients |
| `config/micro_channels.json` | Channel list and dead bands |

### A.6 Worked examples

#### A.6.1 Household evaluation

This example is produced by the actual engine with the configuration in the
repository. It is illustrative, not a real household.

**Inputs:**

- \(x=18.0\) million VND/month;
- \(y_0=20.0\) million VND/month;
- \(w_0=(0.45,0.25,0.12,0.18)\);
- \(n_a=2\), \(n_c=2\);
- liquid assets 15.0, illiquid assets 40.0, debt service 1.2 million VND;
- one macro medoid \(F=(0.5,-0.2,0.3,0.1,0.0)\);
- policy \(u=(1.0,0.5,2.0)\);
- stress probability \(\pi_s=0.20\);
- OECD modified equivalence scale.

**Step 1: prices.**

\[
\Delta\ln p=
F M^{\top}+(-0.012)(3.5)+0.025(0.20)
=(-0.0129,-0.0299,0.0116,-0.0271).
\]

\[
p_1=(0.987183,\;0.970543,\;1.011668,\;0.973264).
\]

**Step 2: equivalence scale.**

\[
D=1+0.5(2-1)+0.3(2)=2.1,
\qquad
\eta=0.08\frac{2}{4}=0.04,
\]

\[
D_{\text{adj}}=2.1(0.987183)^{0.04}=2.098917.
\]

**Step 3: QUAIDS.**

\[
\ln a(p_1)=0.982817,
\qquad
b(p_1)=0.999142,
\]

\[
\ln\tilde{x}=\ln\left(\frac{18}{2.098917}\right)-0.982817=1.166134.
\]

The share update gives

\[
w_1=(0.246792,\;0.118440,\;0.159775,\;0.474992).
\]

The large shift toward discretionary/services is a property of the illustrative
coefficients and the local update, not an empirical forecast.

**Step 4: index numbers.**

\[
P_T=0.982893,\quad
P_L=0.983456,\quad
P_P=0.982314,\quad
P_F=0.982884.
\]

\[
E_{\text{sub}}=7.844644,
\qquad
CV=18(0.982893-1)=-0.307925,
\]

\[
x^{\text{real}}=\frac{18}{2.098917(0.982893)}=8.725112.
\]

**Step 5: income and balance sheet.**

\[
y^{\text{real}}=20.225141,
\qquad
\text{buffer months}=1.912133,
\]

\[
MDSR=0.118164,
\qquad
\text{HANK class}=\text{Wealthy\_Hand\_to\_Mouth\_W\_HtM},
\]

\[
\text{MPC}=0.28,
\qquad
\omega^*=0.
\]

The Merton share is zero because the liquid buffer is below three months.

**Step 6: vulnerability.**

\[
VEP=9.31\times10^{-10},
\qquad
FGT_0=FGT_1=FGT_2=0.
\]

The household is far above the 3.50 million VND poverty line in this scenario.

**Step 7: Carroll–Toche.** The returned target ratio is `51.0`. In this
parameterization the denominator hits its `0.02` floor, so
\(m_{\text{target}}=1+1/0.02=51\). The number is dimensionless, not 51 months.
This is a useful reminder to check the denominator when the target ratio looks
implausibly large.

#### A.6.2 Weighted VaR and ES

Let losses be \((1,2,3,10)\) with weights \((0.50,0.30,0.15,0.05)\) and
\(\alpha=0.90\). Cumulative weights are \((0.50,0.80,0.95,1.00)\). The first
index reaching 0.90 is the third loss, so

\[
\mathrm{VaR}_{0.90}=3.
\]

The boundary atom is \(0.95-0.90=0.05\). The tail mass is

\[
T=0.05(3)+0.05(10)=0.65,
\]

\[
\mathrm{ES}_{0.90}=\frac{0.65}{0.10}=6.5.
\]

The engine uses the same boundary-split logic at \(\alpha=0.99\).

#### A.6.3 Entropy pooling with two particles

Let prior weights be \((0.5,0.5)\), terminal GDP values \((1,3)\), and target
\(\bar{g}=2.5\). The tilt is proportional to
\(p_i e^{\lambda(g_i-\bar{g})}\). The mean constraint is

\[
\frac{1\cdot e^{-1.5\lambda}+3\cdot e^{0.5\lambda}}
{e^{-1.5\lambda}+e^{0.5\lambda}}=2.5.
\]

Let \(r=e^{2\lambda}\). Then

\[
\frac{1+3r}{1+r}=2.5
\;\Longrightarrow\;
r=3
\;\Longrightarrow\;
\lambda=\frac{\ln 3}{2}=0.549306.
\]

The posterior weights are \((0.25,0.75)\). The high-GDP particle receives more
weight because the target is above the prior mean.

### A.7 Validation checklist

Use this checklist before treating a run as reviewable.

**Configuration**

- [ ] `config/pipeline_v2.json` parses and all required sections are present.
- [ ] `config/classifier_calibration.json` exists and its `threshold` and
  `mixed_margin` are the intended operating point.
- [ ] `config/micro_household.json` passes the QUAIDS restrictions.
- [ ] `config/micro_channels.json` has unique ids and supported formula
  handlers.
- [ ] `.env` does not contain placeholder secrets.
- [ ] `SOVEREIGN_SANDBOX_SECRET` is set to a strong value for production.

**Macro data**

- [ ] The panel has exactly 36 columns and 30 rows.
- [ ] No column is entirely missing; `missing_cells` is recorded.
- [ ] The panel `source` is `world_bank_live` or `world_bank_cache`, not a
  synthetic fixture.
- [ ] The cache TTL and stale-cache status are recorded.
- [ ] The core columns match the expected units.

**Macro engine**

- [ ] `calibration.estimation_backend` is
  `pca_var5_hard_em_covariance`.
- [ ] `var_companion_spectral_radius` is below 1, with the shrink flag
  consistent with the value.
- [ ] `nowcast.state_source` is either `rbpf_rts_smoothed_state` or the
  divergence fallback, and `rbpf_diverged` matches.
- [ ] `gatekeeper.joint_valid_particles` is at least `max(50, k_rep)`, or
  `fallback_used` is true.
- [ ] `gatekeeper.entropy_pooling.kish_ess` meets the floor.
- [ ] `wasserstein.cluster_weight_sum` is 1.
- [ ] `F_medoids_terminal` has shape \(K_{\text{rep}}\times5\).
- [ ] `gfevd_matrix` rows sum to 100.
- [ ] `artifact_sha256` is present and the artifact hash verifies.
- [ ] The firm `mode` is `provided_arrow_ipc` or `synthetic_fixture`; a
  synthetic mode is not presented as real firm data.

**Household**

- [ ] The household profile fields have the correct units.
- [ ] `w0` has four shares summing to 1.
- [ ] The equivalence method is one of `oecd_modified`, `oecd_sqrt`, `bls_ses`.
- [ ] Every `computed` channel has a magnitude and `source_or_formula`.
- [ ] Every missing input produces `not_estimated`, not an invented amount.
- [ ] `carroll_toche_target_buffer_ratio` is not interpreted as months.
- [ ] The VEP thresholds are the configured 0.292 and 0.500.
- [ ] The poverty line is the configured 3.50 million VND/month.

**Pipeline**

- [ ] `effective_mode` is one of `macro`, `micro`, `dual`.
- [ ] `dual` has `upstream_macro_used=true` and at least one `computed` channel
  with an `artifact:` source.
- [ ] S5 grounding, consistency, completeness, and mode integrity all pass, or
  the run is quarantined.
- [ ] Purged rows are gone; quarantined rows are retained and reported.
- [ ] `PipelineOutput` does not contain raw cache content or personal context.
- [ ] LLM traces are redacted when `persist_opt_in=false`.

**Reproducibility**

- [ ] The request seed is recorded.
- [ ] The panel cache timestamp is recorded.
- [ ] The artifact hash is recorded.
- [ ] S6 output is labelled as non-deterministic when present.

### A.8 Non-goals and production readiness

The system is not:

- a real-time forecasting service;
- a credit-scoring or lending decision system;
- a tax, benefit, or pension calculator;
- a general-equilibrium model;
- a substitute for audited microdata;
- a guarantee that a stress scenario will or will not occur;
- a confidential computing environment.

Before production use, at minimum:

1. Recalibrate QUAIDS, BPP, EGM, HANK, Merton, and VEP parameters with VHLSS/VES
   or another representative household survey.
2. Replace the deterministic PCA/VAR(5) calibration with the production EM
   backend if the artifact contract is preserved.
3. Validate the macro panel against an independent Vietnam macro database.
4. Backtest the gate thresholds and the adversarial objective on historical
   episodes.
5. Replace the synthetic firm fixture with a governed firm-level dataset.
6. Run the privacy review in Section 8.7.
7. Set a strong HMAC secret and restrict sandbox network access.
8. Establish a model-risk review and a change-control process for coefficients
   and thresholds.

---

## 12. Glossary

**Adversarial shock.** A shock vector chosen to maximize an adverse macro
objective subject to a plausibility ellipsoid.

**Artifact.** The hashed JSON output of the macro engine, including the panel
metadata, calibration, nowcast, shocks, branches, gate, tail risk, GFEVD,
satellites, and hash. The `artifact_sha256` field is an unkeyed integrity hash,
not a digital signature.

**BPP.** Blundell, Pistaferri, and Preston (2008). The permanent/transitory
income decomposition and partial-insurance framework used in the household
income block.

**Buffer stock.** Precautionary saving held to smooth consumption against income
risk. The household block measures liquid buffer months and a Carroll–Toche
target ratio.

**Calibration.** The estimation step that turns the panel into factors,
loadings, VAR coefficients, regime covariances, GPD tails, and transmission
matrices.

**Carroll–Toche target.** A dimensionless target ratio of market resources to
permanent income. It is not a number of months.

**CMA-ES.** Covariance Matrix Adaptation Evolution Strategy. A derivative-free
optimizer used to search for adverse shocks.

**Copula.** A function that couples marginal distributions into a joint
distribution. The macro engine uses a Gaussian copula built from Kendall's tau.

**CRAG.** A tri-state retrieval check that classifies retrieval as correct,
ambiguous, or low confidence, and triggers decomposition or external search.

**CRRA.** Constant relative risk aversion. The household block uses it in the
EGM, Carroll–Toche, and Merton formulas.

**DVI.** A firm-level distress or vulnerability index in the synthetic firm
table. The exact economic definition is not part of this repository; the field
is used as a standardized stress score.

**EGM.** Endogenous Grid Method. A numerical method for solving consumption
saving problems by constructing the policy function on an endogenous grid.

**Elo.** A pairwise rating system. The S6 tournament updates hypothesis ratings
with \(K=32\).

**Entropy pooling.** A minimum-relative-entropy method for imposing views on a
prior distribution. The macro engine imposes one GDP constraint.

**Equivalence scale.** A factor that converts household size and composition
into equivalent adults for consumption comparisons.

**ES.** Expected shortfall. The average loss beyond the VaR threshold.

**FGT.** Foster–Greer–Thorbecke poverty indices: headcount \(P_0\), gap \(P_1\),
and severity \(P_2\).

**Firm downscaling.** The projection of macro scenarios onto firm-level stress
scores using factor betas, policy sensitivities, and a cross-sectional shape
quartet.

**GATE.** The joint validity filter that requires physical bounds, stock-flow
consistency, and economic sign plausibility across all three branches.

**GFEVD.** Generalized forecast-error variance decomposition. The share of a
target series' forecast variance attributable to each source series.

**GPD.** Generalized Pareto distribution. Used for the tails above the 90th
percentile of residual shocks.

**HANK.** Heterogeneous-agent New Keynesian model. The household block uses a
two-asset HANK classification, not a full HANK equilibrium.

**Hicksian CV.** Compensating variation: the nominal amount needed to restore
the pre-shock utility level after a price change. The code reports a
first-order approximation.

**HMAC.** Hash-based message authentication code. The sandbox receipt uses
HMAC-SHA256.

**Joint gate.** The intersection of valid particle sets across baseline,
passive, and active branches. It can fall back to the active branch or all
particles and records the fallback.

**Kish ESS.** Effective sample size \(1/\sum p_i^2\). The engine enforces a
floor to prevent weight collapse.

**LES.** Linear Expenditure System, also called Stone–Geary. Used for the
subsistence floor.

**Mahalanobis distance.** A covariance-adjusted distance used to define the
plausibility ellipsoid for shocks.

**Markov regime.** A discrete state with its own transition probabilities and
covariance. The macro engine uses two regimes.

**MDSR.** Modified debt-service ratio: monthly debt service divided by
supernumerary income (expenditure minus the subsistence floor).

**Medoid.** A representative particle chosen from the simulated set. The
Wasserstein reduction returns 50 medoids with cluster weights.

**Merton share.** The optimal risky-asset share in a myopic mean-variance
portfolio: \(erp/(\rho\sigma^2)\), clipped to \([0,1]\).

**MMR.** Maximal marginal relevance. A retrieval objective that balances query
relevance against redundancy.

**MPC.** Marginal propensity to consume. The household block assigns
regime-specific quarterly MPC values.

**NPL.** Non-performing loan ratio. The macro gate and branch metrics use it as
a financial-stability indicator.

**OMO.** Open market operations. One of the three active-branch policy tools.

**ProbLog.** A probabilistic logic programming language used to compute
hypothesis viability and conflict probabilities.

**QUAIDS.** Quadratic Almost Ideal Demand System. The household block uses a
local share update based on its coefficients.

**RBPF.** Rao-Blackwellized particle filter. The macro engine runs a
conditional Kalman filter per particle and samples the regime.

**RIC/GIC.** Return-impatience condition and growth-impatience condition. They
determine whether the buffer-stock target is finite.

**RTS.** Rauch–Tung–Striebel smoother. The backward pass of the macro filter.

**SFC.** Stock-flow consistency. The macro simulation enforces a reserve
identity with a tolerance and an absorbing floor.

**Sobol sequence.** A low-discrepancy quasi-random sequence. The simulation uses
Owen-scrambled Sobol draws.

**Törnqvist index.** A superlative cost-of-living index using average base and
current shares.

**UCB1.** Upper Confidence Bound 1. A multi-armed-bandit rule used for model
selection in S6.

**VaR.** Value at risk. The loss threshold at a given tail probability.

**VEP.** Vulnerability as expected poverty. The probability that future
consumption falls below the poverty line.

**Wasserstein reduction.** A scenario-reduction step that selects weighted
medoids in a standardized path space.

**Zero-copy.** Reading Arrow columns without copying their buffers into new
memory. Used for firm and population tables.

---

*End of document.*
