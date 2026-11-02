# OSINTViet
An economic leaned, community oriented OSINT website, made for Vietnamese-speakers. Designed to distribute macro terms for regular usage, for the common profit.

## Pipeline V2 (macro → micro → dual)

Pipeline V2 chạy một chuỗi thống nhất: nhận truy vấn/bối cảnh → phân loại → cache
log theo `run_id` → engine vĩ mô hoặc hộ gia đình → kiểm tra lại → purge cache đã
kiểm chứng → orchestrator → JSON. Xem hướng dẫn đầy đủ tại
[`docs/HUONG_DAN_PIPELINE_V2.md`](docs/HUONG_DAN_PIPELINE_V2.md) và trang
`/pipeline-v2` trên website.

```bash
# Chạy dual, JSON ra stdout
.venv/bin/python run_full_workflow.py --mode dual \
  --query "Lạm phát ảnh hưởng hộ gia đình tôi thế nào?" \
  --context-file context.json --household-file household.json

# Chạy thử không ghi DB/artifact và không chạy S6
.venv/bin/python run_full_workflow.py --mode micro \
  --query "Giá thực phẩm tăng ảnh hưởng chi tiêu ra sao?" --dry-run

# Kiểm tra bản ghi cách ly (không bao giờ tự động xóa)
.venv/bin/python run_full_workflow.py quarantine-list
```

BYOK chỉ đọc từ `OSINT_BYOK_API_KEY` hoặc `--byok-stdin`; key chỉ nằm trong bộ
nhớ tiến trình. Bối cảnh cá nhân chỉ được lưu khi `persist_opt_in=true`.

Kiểm thử:

```bash
.venv/bin/python tests/test_pipeline_v2.py
.venv/bin/python tests/test_micro_household_subsystem.py
.venv/bin/python tests/test_macro_stress_v57.py
```

