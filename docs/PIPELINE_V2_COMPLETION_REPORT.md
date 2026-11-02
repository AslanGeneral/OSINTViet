# Pipeline V2 Completion Report

Date: 2026-10-31 (final verification 2026-11-01)
Repository: `/home/lecuirassier/Documents/mirrored_backend`
Scope: `pipeline-v2-macro-micro-dual`

## 1. Outcome

Pipeline V2 is implemented and wired into the existing entry points. A single
request now runs the intended sequence:

```
S1 RawMaterial -> S2 Classifier -> S3 Cache Log (written)
  -> S4 macro/micro/dual engine(s) -> cache consumed_by_engine
  -> S5 revalidating -> verified -> purge (or quarantined)
  -> S6 UniversalCoScientistPipeline -> S7 PipelineOutput
```

The macro v5.7 cluster was reused without changing its internals. The household
subsystem lives in `app/math_engine/micro_household/`, is imported lazily, and
can run alone or consume the signed v5.7 artifact through a read-only bridge.

All three test suites pass, and a 10-case stress matrix completed with 10/10
expected exit codes. The first full-S6 stress run exposed a trace-recorder bug;
it was fixed. The post-fix matrix then hit the old 1800 s timeout, so the
timeout was raised and the full-S6 case was rerun in isolation; that rerun
completed S6 with a non-null orchestration result (section 6.4).

## 2. Phase changes

| Phase | Delivered |
| --- | --- |
| P0 | `discovery_report.md`; baseline tests and module map; pre-existing user work identified as read-only. |
| P1 | `app/pipeline_v2/contracts.py`, `errors.py`: strict public contracts, `StructuredError`, stable exit codes, secret redaction. |
| P2 | `app/pipeline_v2/classifier.py`, calibration JSON: deterministic lexicon + existing embedding prototypes; threshold from calibration, not code. |
| P3 | `app/models/cache_log.py`, `app/pipeline_v2/cache_log.py`: SQLModel tables on the existing SQLite engine; lifecycle transitions, atomic per-run purge, audit without content. |
| P4 | `app/pipeline_v2/macro_adapter.py`, `micro_engine.py`, `mode_router.py`, and `app/math_engine/micro_household/*`: macro adapter, micro channels, dual routing, household equations. |
| P5 | `app/pipeline_v2/revalidation.py`: grounding, consistency, completeness, mode integrity, quarantine, dry-run, idempotent purge. |
| P6 | `app/pipeline_v2/orchestrator.py`, `input_fetch.py`, `byok_router.py`, `cli.py`, API route, Vietnamese guide, entry-point dispatch. |

New code is isolated under `app/pipeline_v2/`, `app/math_engine/micro_household/`,
`app/models/cache_log.py`, `app/api/v1/pipeline_v2.py`, `config/`, `scripts/`,
`tests/` and `docs/`.

## 3. Privacy and secret handling

- BYOK is read only from `OSINT_BYOK_API_KEY` or explicit `--byok-stdin`; the
  key is held in process memory and added to the redaction set. It is never put
  in config, cache content, audit rows, artifacts, logs or error payloads.
- `PersonalContext` is persisted only when `persist_opt_in=true`. Cache rows
  store the context payload only under that opt-in and are purged after
  re-validation.
- Opt-out artifacts no longer leak context-derived values:
  - `raw_material.json` drops `query_breakdown.personal_dimensions` and
    `query_breakdown.explicit_parameters`, and records
    `personal_parameters_redacted: true`.
  - `llm_calls.json` drops `user_prompt_excerpt`, `response_excerpt`, `parsed`,
    `validation_error` and `transport_error`; the S1 `query_breakdown` record
    loses its prompt hashes and is marked `prompt_redacted: true`.
  - Prompt/response hashes and call metadata remain for non-S1 calls so the
    audit trail stays verifiable without exposing content; the S1
    `query_breakdown` call keeps metadata only.
- `--household-file` is an explicit numeric input separate from
  `PersonalContext`. Micro results may quote those numbers in computed
  magnitudes to explain the formula; this is documented in the Vietnamese
  guide.
- Quarantine inspection omits `content` by design; quarantined rows are never
  auto-purged and require `quarantine-list` / `quarantine-delete --yes`.

## 4. Configuration moved out of code

No economic coefficient or operating threshold remains hard-coded in the new
runtime modules. The values below are injected from JSON:

| Config file | Values |
| --- | --- |
| `config/pipeline_v2.json` | classifier/revalidation thresholds, cache limits, query-breakdown terms, BYOK default model/base URL/timeout, orchestrator timeout |
| `config/classifier_calibration.json` | calibrated threshold `0.51`, mixed margin `0.02`, metrics |
| `config/micro_household.json` | QUAIDS/BPP/EGM/HANK coefficients, `equivalence_scales` (OECD-modified, BLS-SES, child-food eta), `price_projection_clip_ln`, VEP thresholds `0.292`/`0.500`, BPP price pass-through, buffer/MDSR thresholds, MPC by regime |
| `config/micro_channels.json` | channel list/formulas, global direction epsilon, per-channel direction epsilons |

`BYOKConfig.default_model` and `default_base_url` are now required fields
supplied by `config/pipeline_v2.json` instead of schema defaults. The EGM /
buffer-stock constructor no longer carries coefficient defaults either; the
bridge must pass every value from `config/micro_household.json`.

## 5. Tests

Commands and results:

```
.venv/bin/python tests/test_pipeline_v2.py
  Ran 24 tests ... OK

.venv/bin/python tests/test_micro_household_subsystem.py
  Ran 10 tests ... OK

.venv/bin/python tests/test_macro_stress_v57.py
  [test] RFC 8785-compatible JCS + strict request contract: OK
  [test] v5.7 engine, gatekeeper, micro Arrow bridge, plugin: OK
  [test] live World Bank panel: SKIP (use --live)
  [test] ALL OK
```

New regression coverage added during verification:

- opt-out `raw_material.json` and `llm_calls.json` redaction;
- dual mode integrity requires at least one computed channel whose
  `source_or_formula` contains `artifact:`;
- trace extraction works with the real `_TraceRecorder` object;
- auto-mode structured errors report the classifier recommendation as
  `mode_used`;
- CLI usage errors are emitted as `StructuredError` JSON on stderr;
- macro claims use Vietnamese templates instead of the macro engine's English
  `deterministic_briefing` lines;
- secret redaction uses a non-key-looking synthetic probe.

A secret scan over the new pipeline, micro, config, docs, scripts and tests
found no key-shaped strings. The only remaining assignment-pattern match is the
intentional `synthetic-redaction-probe` fixture used to test redaction.

## 6. Stress evidence

The reproducible runner is `scripts/stress_pipeline_v2.py`. It uses synthetic
Vietnamese inputs, invokes the real CLI in a subprocess, and writes
`summary.json` plus per-case stdout/stderr.

Example:

```bash
.venv/bin/python scripts/stress_pipeline_v2.py \
  --output-dir /tmp/opencode/stress_postfix
```

### 6.1 Pre-fix matrix (10/10 expected exits)

| Case | Exit | Mode | Validation | Purge | S6 | Duration |
| --- | --- | --- | --- | --- | --- | --- |
| `01_macro_persistent_skip_s6` | 0 | macro | passed | committed, 2 | skipped | 58.5s |
| `02_micro_persistent_optout` | 0 | micro | passed | committed, 2 | skipped | 12.4s |
| `03_dual_persistent_optin` | 0 | dual | passed | committed, 2 | skipped | 71.5s |
| `04_auto_macro_skip_s6` | 0 | macro | passed | committed, 2 | skipped | 63.3s |
| `05_auto_mixed_allow_dual` | 0 | dual | passed | committed, 2 | skipped | 67.3s |
| `06_micro_missing_household` | 0 | micro | passed | committed, 2 | skipped | 12.6s |
| `07_macro_human_output` | 0 | macro | n/a (human) | n/a (human) | skipped | 64.3s |
| `08_auto_ambiguous_expected_error` | 2 | error path | n/a | n/a | skipped | 6.0s |
| `09_dual_dry_run` | 0 | dual | passed | dry-run, 0 | skipped | 71.5s |
| `10_dual_full_s6` | 0 | dual | passed | committed, 2 | attempted | 1332.2s |

The first full-S6 run produced `orchestration: null` with warning
`TypeError: '_TraceRecorder' object is not iterable`. S6 itself had completed and
written `orchestration_output.json`, but the wrapper failed while copying the
trace. The wrapper now reads `trace_obj.records` when present; a regression test
covers the real recorder shape.

### 6.2 Post-fix matrix (10/10 expected exits)

| Case | Exit | Mode | Validation | Purge | S6 | Duration |
| --- | --- | --- | --- | --- | --- | --- |
| `01_macro_persistent_skip_s6` | 0 | macro | passed | committed, 2 | skipped | 65.4s |
| `02_micro_persistent_optout` | 0 | micro | passed | committed, 2 | skipped | 12.1s |
| `03_dual_persistent_optin` | 0 | dual | passed | committed, 2 | skipped | 57.7s |
| `04_auto_macro_skip_s6` | 0 | macro | passed | committed, 2 | skipped | 53.9s |
| `05_auto_mixed_allow_dual` | 0 | dual | passed | committed, 2 | skipped | 53.3s |
| `06_micro_missing_household` | 0 | micro | passed | committed, 2 | skipped | 11.1s |
| `07_macro_human_output` | 0 | n/a | n/a (human) | n/a (human) | skipped | 50.2s |
| `08_auto_ambiguous_expected_error` | 2 | micro | n/a | n/a | skipped | 4.5s |
| `09_dual_dry_run` | 0 | dual | passed | dry-run, 0 | skipped | 52.6s |
| `10_dual_full_s6` | 0 | dual | passed | committed, 2 | timed out at 1800s | 1853.9s |

`10_dual_full_s6` (`run_119e8a29034e4bf1`) reached S6 and returned the documented
graceful timeout result: status `ok_with_warnings`, `orchestration: null`, no
`llm_calls.json`, with S1-S5 still validated and purged. Local Ollama took
longer than the original 1800 s budget. The orchestrator timeout is now 2700 s
in `config/pipeline_v2.json`, and the full-S6 case was rerun to verify the
wrapper trace fix (section 6.4).

### 6.3 Privacy verification (opt-in vs opt-out)

The post-fix matrix exercised both persistence choices with the same personal
context and household file:

| Case | Run | `persist_opt_in` | `raw_material.json` query breakdown | Purge |
| --- | --- | --- | --- | --- |
| `02_micro_persistent_optout` | `run_56a0df162cb042e7` | false | only `personal_parameters_redacted=true`; no explicit parameters or dimensions | committed, 2 |
| `03_dual_persistent_optin` | `run_ea7e21484f924d3e` | true | `explicit_parameters` = `{monthly_income_million_vnd: 26.0, monthly_expenditure_million_vnd: 20.0, liquid_assets_million_vnd: 24.0, n_adults: 2, n_children: 2}` and `personal_dimensions` present | committed, 2 |
| `05_auto_mixed_allow_dual` | `run_b0fa351b80da4419` | false | redacted marker only | committed, 2 |
| `10_dual_full_s6` | `run_119e8a29034e4bf1` | false | redacted marker only | committed, 2 |

The opt-out runs contain none of the context amounts and no
`personal_dimensions` key. The opt-in run retains them exactly as supplied. All
four runs produced valid JSON on stdout with progress text on stderr.

The separate `--household-file` is a computational input: values such as
`monthly_income_million_vnd = 22.0` appear in `micro_bundle.json` because they
enter the cited formulas. This is not context persistence and is documented as a
limitation in section 8.

### 6.4 Full-S6 rerun after the trace fix

The full-S6 case was rerun alone after raising the orchestrator timeout from
1800 s to 2700 s:

```bash
.venv/bin/python scripts/stress_pipeline_v2.py \
  --output-dir /tmp/opencode/stress_s6_rerun --case 10_dual_full_s6
```

| Field | Result |
| --- | --- |
| `run_id` | `run_a37c3f2b4f224d40` |
| Exit / status | `0` / `ok_with_warnings` |
| Duration | 1,241.2 s total; about 1,181.7 s in S6 |
| Classifier | `mixed`; mode `dual` |
| S5 validation | 4/4 checks passed (`grounding`, `consistency`, `completeness`, `mode_integrity`) |
| Purge | `committed`, 2 rows; audit `audit_6ad8901df6d3408d88f8` |
| LLM trace | `llm_calls.json` present, 8 calls, 4.7 KB, no prompt/response content |
| Orchestration | non-null: `task_51541237`, 1 viable branch, 0 dead branches, 0 negative rules |
| S6 warnings | none: no timeout and no `_TraceRecorder` warning |

The remaining warnings are the expected no-live-article notice and two
`optimal_policy_u` assumption notices from the macro artifact. The trace
records are the eight S6 calls (`structuring_worker`, four `worker` calls,
`practical_critic`, `theoretical_critic`, `sandbox_worker`) and contain only
metadata/hashes under opt-out; the verifier found no content keys and no S1
prompt hashes. The opt-out `raw_material.json` remained redacted.

The viable branch (`gen_1_hyp_1`) carries a sandbox receipt, so S6 produced an
auditable result rather than a synthesized one. The earlier pre-fix run's
`_TraceRecorder` warning is gone, and the wrapper returned the summary metadata
rather than `orchestration: null`.

## 7. Bugs found during verification and fixed

1. **Opt-out artifact leak**: `raw_material.json` contained context-derived
   `explicit_parameters`/`personal_dimensions`. Fixed with
   `_artifact_safe_raw_material`; regression test added.
2. **Opt-out LLM trace leak risk**: S1 prompts can embed personal context.
   Fixed with `_artifact_safe_trace`, which strips content and the S1 prompt
   hashes when opt-out; regression test added.
3. **Dual mode integrity too weak**: dual passed with no computed micro channel.
   Now requires at least one computed channel whose `source_or_formula` contains
   `artifact:`; regression test added.
4. **Trace recorder incompatibility**: full S6 failed after completion because
   `trace` is a `_TraceRecorder`, not a list. Wrapper now accepts both.
5. **Auto-mode error label**: `mode_used` defaulted to `macro` on
   `MODE_SELECTION_REQUIRED`. It now reflects the classifier recommendation.
6. **CLI stdout pollution**: incidental library/plugin prints are redirected to
   stderr while pipeline/quarantine work runs, so stdout stays valid JSON.
7. **Key-shaped test fixture**: replaced `sk-...` with
   `synthetic-redaction-probe`.
8. **Residual hard-coded values**: equivalence scales/eta, VEP thresholds,
   price-projection clip, channel direction epsilons, BPP price pass-through,
   fallback buffer threshold and BYOK defaults moved to config. The EGM/buffer
   constructor defaults were removed so the config is mandatory.
9. **English macro briefing leaked to users**: the adapter copied
   `deterministic_briefing` verbatim into `MacroResult.claims`. It now renders
   the same artifact facts with Vietnamese templates; a regression test checks
   that the English lines cannot reappear.
10. **Argparse errors were unstructured**: invalid/missing flags printed usage
    text. The CLI parser now raises `InputError` and emits JSON on stderr while
    keeping `--help` intact.

## 8. Limitations and open questions

- **Classifier calibration**: the threshold/margin were fitted on 46 synthetic
  labelled samples with perfect training metrics. Re-calibrate on audited
  production pairs before tightening; treat `0.51` as an initial operating
  point.
- **Micro coefficients**: QUAIDS/BPP/EGM/HANK values are placeholders supplied
  with the specification. They must be re-estimated with VHLSS/VES before
  production use.
- **`optimal_policy_u`**: signed v5.7 artifacts do not publish it; the bridge
  uses a zero vector and records an explicit warning. This is an assumption,
  not a policy estimate.
- **Carroll-Toche units**: `carroll_toche_target_buffer_ratio` is dimensionless
  (`m_check^e`), not months. The earlier "months" label was removed.
- **Full S6 cost**: the isolated local-Ollama full-S6 rerun took 1,241.2 s
  total (about 20.7 minutes), of which about 1,181.7 s was S6. It produced one
  viable Pareto branch, no dead branches and no newly compiled negative rules.
  Runtime is dominated by eight sequential local LLM calls, so the timeout is
  configurable and currently 2700 s in `config/pipeline_v2.json`.
- **S6 failure semantics**: an S6 exception is returned as
  `ok_with_warnings` with `orchestration: null`. If callers require S6 success
  as a hard gate, that policy needs an explicit decision.
- **Household-file echo**: computed micro magnitudes quote the explicit
  household inputs used in the formula. This is separate from opt-out context
  persistence but should be explained to users.
- **Purge safety**: automatic purge only deletes `verified` rows for the same
  `run_id`; a second call is `idempotent_noop`. Quarantined rows require manual
  deletion.
- **No commits**: the working tree already contained extensive user work in
  tracked files (`app/main.py`, `run_full_workflow.py`, `.env.example`,
  `README.md`, macro v5.7 code, templates, static assets). New and pre-existing
  changes are intertwined, so committing would be unsafe. Nothing was
  committed.

## 9. Verification commands

```bash
.venv/bin/python tests/test_pipeline_v2.py
.venv/bin/python tests/test_micro_household_subsystem.py
.venv/bin/python tests/test_macro_stress_v57.py
.venv/bin/python scripts/stress_pipeline_v2.py --skip-full-s6
.venv/bin/python scripts/stress_pipeline_v2.py
.venv/bin/python scripts/stress_pipeline_v2.py \
  --output-dir /tmp/opencode/stress_s6_rerun --case 10_dual_full_s6
.venv/bin/python run_full_workflow.py quarantine-list --output json
.venv/bin/python run_full_workflow.py quarantine-delete --dry-run --output json
```
