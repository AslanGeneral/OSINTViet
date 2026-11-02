# Hướng dẫn Pipeline V2 (macro → micro → dual)

Tài liệu này mô tả cách vận hành pipeline V2 sau khi hoàn tất P0–P6. Toàn bộ
văn bản dành cho người dùng cuối là tiếng Việt; mã nguồn, comment và báo cáo kỹ
thuật giữ tiếng Anh.

## 1. Kiến trúc và thứ tự chạy

Mỗi lần chạy đi qua đúng bảy tầng:

1. **S1 – Input/LLM fetch**: nhận `RawInput`, tạo `RawMaterial`, phân rã truy vấn.
   Mặc định chỉ dùng truy vấn và panel World Bank đã cache. Bài báo live là tùy
   chọn (`--live`) và tái sử dụng `HeadlessEconArticleFetcher` +
   `FullCircleWorkflow._analyze_and_filter`.
2. **S2 – Classifier**: lexicon tất định + embedding prototype hiện có. Ngưỡng
   lấy từ `config/classifier_calibration.json`; không hard-code.
3. **S3 – Cache Log**: ghi nội dung đã xử lý và vector (nếu có) vào bảng
   `cache_log_entries` trên SQLite hiện có. Bối cảnh cá nhân chỉ được lưu khi
   `persist_opt_in=true`.
4. **S4 – Econ-Engine**: macro (adapter mỏng quanh engine v5.7), micro (hộ gia
   đình, chạy độc lập được), hoặc dual (macro xong mới chạy micro).
5. **S5 – Re-validation + purge**: kiểm tra grounding, consistency,
   completeness, mode integrity; chỉ purge khi tất cả đạt.
6. **S6 – Orchestrator hiện có**: `UniversalCoScientistPipeline`, dùng lại
   embedding/router/plugin hiện có; ingestor thô tạm tắt.
7. **S7 – Output**: `PipelineOutput` JSON, không chứa nội dung cache thô và
   không chứa bối cảnh cá nhân.

Sơ đồ:

```
S1 RawMaterial → S2 Classifier → S3 Cache Log (written)
   → S4 macro/micro/dual → Cache (consumed_by_engine)
   → S5 revalidating → verified → purge (hoặc quarantined)
   → S6 Orchestrator → S7 PipelineOutput
```

## 2. Chế độ

| Mode | Hành vi |
| --- | --- |
| `macro` | Chỉ chạy engine v5.7; không trả `MicroResult`. |
| `micro` | Chỉ chạy engine hộ gia đình; không cần macro; kênh thiếu dữ liệu là `not_estimated`. |
| `dual` | Chạy macro trước, truyền payload đã ký sang micro; macro lỗi thì micro không chạy. |
| `auto` | Dùng classifier. `mixed` hoặc confidence dưới ngưỡng sẽ báo lỗi có cấu trúc, trừ khi có `--allow-dual`. |

Mode do người dùng chọn luôn thắng. Classifier chỉ gợi ý.

## 3. Bối cảnh cá nhân và hồ sơ hộ

`PersonalContext` (tệp `--context-file`) chỉ có các trường văn bản:

```json
{
  "free_text": "Thu nhập 20 triệu, chi tiêu 15 triệu, tiết kiệm 60 triệu",
  "region": "Hà Nội",
  "occupation_sector": "giáo dục",
  "household_notes": "2 người lớn, 1 trẻ em",
  "assets_liabilities_notes": "Có khoản vay mua nhà",
  "persist_opt_in": false
}
```

Số liệu hộ tường minh nên đặt trong `HouseholdProfile` (`--household-file`):

```json
{
  "monthly_income_million_vnd": 22.0,
  "monthly_expenditure_million_vnd": 18.0,
  "budget_shares_w0": [0.45, 0.25, 0.12, 0.18],
  "n_adults": 2,
  "n_children": 1,
  "liquid_assets_million_vnd": 18.0,
  "illiquid_assets_million_vnd": 450.0,
  "monthly_debt_service_million_vnd": 3.2,
  "crra_rho": 2.5
}
```

Quy tắc:

- Không nhập API key vào hai tệp trên.
- `budget_shares_w0` phải có đúng 4 phần và tổng bằng 1.
- Thiếu trường nào thì khối/kênh tương ứng là `not_estimated`; hệ thống không
  tự bịa số.
- `persist_opt_in=false` (mặc định): bối cảnh chỉ nằm trong bộ nhớ của lần chạy.
  Artifact `raw_material.json` không chứa `personal_dimensions`/
  `explicit_parameters`, và trace `llm_calls.json` không chứa prompt/response;
  riêng lời gọi S1 chỉ còn metadata kỹ thuật, các lời gọi khác giữ hash để kiểm
  toán mà không lộ nội dung.
- `persist_opt_in=true`: bối cảnh được lưu cùng cache log và bị purge sau khi
  kiểm chứng.
- `--household-file` là đầu vào số học tường minh do người dùng cung cấp, tách
  khỏi `PersonalContext`; kết quả micro có thể nhắc lại các con số này để giải
  thích công thức.

## 4. BYOK

Key chỉ nằm trong bộ nhớ tiến trình:

```bash
export OSINT_BYOK_API_KEY="..."          # không ghi vào tệp
export OSINT_BYOK_BASE_URL="https://api.openai.com/v1"   # tùy chọn
export OSINT_BYOK_MODEL="gpt-4o-mini"                    # tùy chọn

# Hoặc đọc đúng một dòng từ stdin, không echo:
printf '%s\n' "$OSINT_BYOK_API_KEY" | \
  .venv/bin/python run_full_workflow.py run --mode macro --query "..." --byok-stdin
```

Không có key thì pipeline dùng router hiện có (Ollama cục bộ hoặc key pool đã
cấu hình). Key không bao giờ đi vào cache, audit, log, lỗi hay database.

## 5. Dòng lệnh

```bash
# Chạy đầy đủ, JSON ra stdout
.venv/bin/python run_full_workflow.py --mode dual \
  --query "Lạm phát và lãi suất ảnh hưởng hộ gia đình tôi thế nào?" \
  --context-file context.json --household-file household.json

# Chạy thử: không ghi DB/artifact, không purge thật, không chạy S6
.venv/bin/python run_full_workflow.py --mode micro \
  --query "Giá thực phẩm tăng ảnh hưởng chi tiêu ra sao?" \
  --context-file context.json --dry-run --skip-orchestrator

# Xuất văn bản dễ đọc
.venv/bin/python run_full_workflow.py --mode macro \
  --query "Lạm phát CPI" --output human

# Kiểm tra / xóa bản ghi cách ly (không bao giờ tự động)
.venv/bin/python run_full_workflow.py quarantine-list
.venv/bin/python run_full_workflow.py quarantine-delete --run-id run_... --dry-run
.venv/bin/python run_full_workflow.py quarantine-delete --run-id run_... --yes
```

Quy ước:

- JSON máy đọc được ra **stdout**; log/tiến trình/lỗi ra **stderr**.
- Lỗi dùng `StructuredError` và mã thoát khác 0; không có prompt tương tác.
- `--dry-run` bỏ S6 và không ghi DB/artifact; S1–S5 vẫn chạy với cache trong bộ
  nhớ và báo `purge.dry_run=true`.
- `--skip-orchestrator` bỏ S6 nhưng vẫn ghi DB/artifact (khác `--dry-run`).

## 6. Vòng đời cache và purge

```
written → consumed_by_engine → revalidating → verified → purged
                                      └──────→ quarantined
written / consumed_by_engine ────────────────→ quarantined
```

- Purge chỉ chạy từ `verified`, theo từng `run_id`, nguyên tử và idempotent.
- Lần purge thứ hai trả `idempotent_noop`, không xóa gì thêm.
- `quarantined` không bao giờ bị purge tự động; dùng `quarantine-list` và
  `quarantine-delete --yes`.
- Audit `cache_purge_audits` chỉ chứa `run_id`, số lượng, thời điểm, kết quả;
  không chứa nội dung.
- `--dry-run` báo số bản ghi `verified` sẽ bị xóa nhưng không xóa.

## 7. Kiểm tra lại (S5)

Bốn kiểm tra, tất cả phải đạt:

1. **Grounding**: mỗi claim macro có support là entry id hoặc nguồn trích dẫn;
   mỗi kênh micro `computed`/`sourced` có `source_or_formula`.
2. **Consistency**: cosine embedding (chuẩn hóa [0,1]) hoặc Jaccard fallback
   giữa đầu ra và cache; ngưỡng trong `config/pipeline_v2.json`.
3. **Completeness**: đủ trường bắt buộc của `MacroResult`/`MicroResult`.
4. **Mode integrity**: macro không trả micro; micro không trả macro; dual yêu cầu
   `upstream_macro_used=true` và ít nhất một kênh computed tham chiếu
   `artifact:`.

Không đạt bất kỳ mục nào → toàn bộ entry của run chuyển `quarantined`, không
purge. Cache rỗng cũng fail-closed.

## 8. Cấu hình

| Tệp | Nội dung |
| --- | --- |
| `config/pipeline_v2.json` | ngưỡng classifier, revalidation, cache, BYOK (kể cả model/base URL mặc định), orchestrator |
| `config/classifier_calibration.json` | threshold/margin đã hiệu chuẩn + metrics |
| `config/micro_household.json` | hệ số QUAIDS/BPP/EGM/HANK, M_price, equivalence scale, clip giá, ngưỡng VEP/buffer/MDSR |
| `config/micro_channels.json` | danh sách kênh truyền dẫn, formula handler và dung sai hướng |
| `.env.example` | tên biến môi trường BYOK (không chứa giá trị) |

Không sửa số liệu kinh tế trong code. Mọi ngưỡng/hệ số phải nằm trong config.

## 9. Engine hộ gia đình (micro)

Thư viện nằm ở `app/math_engine/micro_household/`, lazy-load:

- `coli_quaids_equivalence.py`: QUAIDS, Stone-Geary LES, Tornqvist/Fisher/
  Laspeyres/Paasche, Hicks CV, equivalence scale.
- `bpp_income_insurance.py`: BPP GMM (M17–M18) và truyền dẫn thu nhập M19.
- `buffer_stock_egm_hank_vep.py`: Carroll-Toche, EGM, HANK hai tài sản, Merton,
  FGT, VEP.
- `bridge_eco_engine.py`: nhận payload v5.7 read-only, chạy single household
  hoặc population zero-copy Arrow.

Lưu ý về đơn vị: `carroll_toche_target_buffer_ratio` là tỷ lệ market resources
trên permanent income (dimensionless, `m_check^e`), **không phải số tháng**.
Muốn đổi sang tháng phải dùng thêm thu nhập/chi tiêu của hộ và ghi rõ công thức;
pipeline không tự đổi để tránh tạo số liệu không có cơ sở.

## 10. Kiểm thử

```bash
.venv/bin/python tests/test_pipeline_v2.py
.venv/bin/python tests/test_micro_household_subsystem.py
.venv/bin/python tests/test_macro_stress_v57.py
```

Bộ test pipeline dùng embedding client lỗi có chủ đích để chạy fallback từ vựng,
không gọi LLM/mạng/DB thật. Bộ test micro kiểm tra công thức, thiếu đầu vào,
BPP, EGM và Arrow zero-copy.

Chạy ma trận stress 10 kịch bản (macro, micro opt-out/opt-in, dual, auto,
dry-run, lỗi có cấu trúc và một lượt S6 đầy đủ):

```bash
.venv/bin/python scripts/stress_pipeline_v2.py
# Bỏ lượt S6 chậm nhất:
.venv/bin/python scripts/stress_pipeline_v2.py --skip-full-s6
```

Lượt S6 đầy đủ với Ollama cục bộ mất khoảng 20 phút (1.241 giây trong lần
kiểm chứng cuối); timeout được cấu hình trong `config/pipeline_v2.json`.

## 11. Giới hạn và hiệu chuẩn

- Hệ số QUAIDS/BPP/EGM/HANK trong `config/micro_household.json` là placeholder
  do đặc tả cung cấp; phải hiệu chuẩn lại bằng VHLSS/VES trước khi dùng sản xuất.
- Artifact v5.7 không công bố `optimal_policy_u`; pipeline dùng vector 0 và ghi
  giả định. Không tạo số liệu chính sách.
- Ngưỡng classifier/revalidation là điểm vận hành ban đầu; hiệu chuẩn lại trên
  cặp dữ liệu đã kiểm toán trước khi siết.
- Nếu artifact thiếu `F_medoids_terminal`/`cluster_weights_q`, dual/micro dừng
  với lỗi có cấu trúc; không fallback sang số giả.
