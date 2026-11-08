# Use of Artificial Intelligence / Sử dụng Trí tuệ Nhân tạo

> Tài liệu song ngữ. Phần 1 bằng tiếng Việt; Phần 2 bằng tiếng Anh.
>
> Bilingual document. Part 1 is Vietnamese; Part 2 is English.

Tài liệu này giải thích AI được dùng ở đâu trong OSINTViet, chỗ nào hệ thống cố
ý **không** dùng AI, và vì sao đưa câu hỏi thẳng vào một chatbot công cộng không
tạo ra cùng loại kết quả. Đây không phải tuyên bố rằng AI là không cần thiết.
Hệ thống dùng AI cho những việc ngôn ngữ và so khớp ngữ nghĩa; phần tính toán,
bằng chứng và kiểm định do mã nguồn và dữ liệu đảm nhiệm.

This document explains where AI is used in OSINTViet, where the system
deliberately does **not** use it, and why pasting a question into a public
chatbot does not produce the same kind of result. It is not a claim that AI is
unnecessary. The system uses AI for language and semantic-similarity work;
arithmetic, evidence and quality control stay in code and data.

## Nội dung / Contents

- [Phần 1 — Tiếng Việt](#phan-1)
  - [1. AI trong nhà máy: giải thích cho người dùng](#phan-1-muc-1)
  - [2. Kiến trúc kỹ thuật: AI được đặt ở đâu](#phan-1-muc-2)
- [Part 2 — English](#phan-2)
  - [1. AI in the factory: a plain-language explanation](#phan-2-muc-1)
  - [2. Technical architecture: where AI sits](#phan-2-muc-2)

---

<a id="phan-1" name="phan-1"></a>

## Phần 1 — Tiếng Việt

<a id="phan-1-muc-1" name="phan-1-muc-1"></a>

### 1. AI trong nhà máy: giải thích cho người dùng

Hãy hình dung OSINTViet là một nhà máy nhỏ.

- **Nguyên liệu đầu vào** là câu hỏi của bạn và, nếu có, hồ sơ hộ gia đình:
  thu nhập, chi tiêu, tài sản, nợ và số người.
- **Sản phẩm đầu ra** là một báo cáo JSON ghi rõ nguồn, công thức, giả định,
  cảnh báo và kết quả kiểm tra.

Trong nhà máy đó có ba loại "người":

1. **Máy móc** — các đoạn mã tính toán cố định. Cùng một đầu vào và cùng cấu
   hình thì cho cùng một kết quả.
2. **Thủ kho** — nơi lưu nguyên liệu đã xử lý theo từng lô, mỗi lô có mã riêng.
3. **Một nhân viên thư ký mới** — đây là AI. Thư ký đọc nhanh, viết tốt, biết
   tóm tắt và đề xuất ý tưởng. Nhưng thư ký **không được phép bấm máy tính**
   thay cho máy móc, và **không được ký phiếu kiểm định** thay cho trạm kiểm
   định.

Dây chuyền có bảy trạm:

| Trạm | Việc thật trong hệ thống | AI có làm không? |
| --- | --- | --- |
| **S1 — Cổng tiếp nhận** | Đọc câu hỏi, tách từ khóa, ghi nhận số liệu hộ. Khi bật chế độ lấy bài báo trực tuyến, hệ thống đọc bài và trích bằng chứng. | Có, nhưng chỉ hỗ trợ đọc và đề xuất chủ đề/từ khóa. Khi tách câu hỏi, mọi con số do AI trả về đều bị bỏ. Khi đọc bài, mọi nhận định phải kèm câu trích nguyên văn, và con số chỉ được giữ nếu nhìn thấy trong bài. |
| **S2 — Phòng phân loại** | Dán nhãn câu hỏi là vĩ mô, vi mô hay hỗn hợp. | Có, nhưng chỉ là **gợi ý**. Phần từ điển đếm từ là máy; phần "hiểu nghĩa" dùng một mô hình so khớp ngữ nghĩa. Nếu bạn tự chọn chế độ, lựa chọn của bạn thắng. |
| **S3 — Kho theo lô** | Ghi nguyên liệu đã xử lý vào kho theo mã lần chạy. | Không. |
| **S4 — Xưởng tính toán** | Chạy máy tính kinh tế vĩ mô (dữ liệu Việt Nam, kịch bản cú sốc, mô phỏng chính sách) và máy tính hộ gia đình (chi tiêu, thu nhập, tiết kiệm, đói nghèo). | **Không.** Đây là nơi con số được tạo ra. AI không được thay máy tính và không được sửa kết quả. |
| **S5 — Trạm kiểm định** | Kiểm tra căn cứ, tính nhất quán, độ đầy đủ và đúng chế độ. | Chỉ dùng so khớp ngữ nghĩa khi có thể; nếu không, dùng cách so khớp từ. Kết luận đạt/không đạt do luật kiểm tra quyết định. |
| **S6 — Phòng nghiên cứu** | Đọc tài liệu, đề xuất giả thuyết, phản biện, chấm điểm, chạy thử các ràng buộc số học trong một môi trường tách biệt (hộp cát). | **Có, đây là nơi AI làm việc nhiều nhất.** Nhưng đây là phòng ý tưởng, không phải dây chuyền sản xuất con số. |
| **S7 — Đóng gói** | Xuất JSON cuối cùng, không kèm nguyên liệu thô hay dữ liệu cá nhân. | Không. |

**Một ví dụ.** Bạn hỏi: "Giá thực phẩm tăng 20% thì hộ tôi bị ảnh hưởng thế
nào?" Nhà máy lấy số liệu hộ của bạn, chạy máy tính chi tiêu hộ gia đình (mô
hình giá, thu nhập và tiết kiệm), rồi trả về các kênh tác động. AI có thể giúp
đọc câu hỏi, đề xuất cách diễn đạt và đưa ra giả thuyết về cơ chế. Nhưng con số
cuối cùng đến từ máy tính hoặc từ một nguồn được trích dẫn. Nếu thiếu dữ liệu,
báo cáo ghi `not_estimated` — nghĩa là "không đủ thông tin", chứ không phải số 0
và cũng không phải một con số đoán cho đẹp.

#### Vì sao không đưa câu hỏi thẳng cho ChatGPT hay Gemini?

Hãy tưởng tượng bạn gọi điện cho một người tư vấn bên ngoài, mô tả nhà máy qua
điện thoại, rồi nhận lại một báo cáo viết rất trôi chảy. Báo cáo có thể hữu
ích để hiểu khái niệm, nhưng:

- **Không có máy nào chạy.** Người tư vấn không vận hành xưởng tính toán. Họ
  viết bằng kinh nghiệm và mẫu câu, nên con số có thể chỉ là con số "nghe hợp
  lý".
- **Không có sổ lô.** Không ai biết báo cáo đã dùng dữ liệu nào, công thức nào,
  giả định nào. Không thể chạy lại để kiểm tra.
- **Không có trạm kiểm định.** Không có cơ chế bắt buộc phải từ chối khi thiếu
  căn cứ. Không có `not_estimated`, không có cách ly lô lỗi, không có chứng nhận
  chạy thử.
- **Không có hàng rào dữ liệu.** Hồ sơ thu nhập, nợ và chi tiêu của bạn được
  gửi ra ngoài theo điều khoản của nhà cung cấp. Họ có thể lưu hoặc dùng dữ
  liệu đó tùy chính sách.
- **Không có kiểm soát phiên bản.** Nhà cung cấp có thể đổi mô hình mà không
  báo. Cùng một câu hỏi có thể cho câu trả lời khác, và bạn không có bản ghi để
  biết vì sao.

Nói cách khác: chatbot công cộng là một người viết báo cáo giỏi. Nó không phải
là nhà máy, không phải cái máy tính, và không phải người ký chứng nhận. Dùng nó
để **hiểu khái niệm** thì hợp lý; dùng nó để **lấy con số cho một quyết định
thật** là đặt sai vai trò.

> **Tóm lại:** AI trong OSINTViet là người thư ký và người gợi ý ý tưởng. Máy
> móc làm phép tính, trạm kiểm định giữ chất lượng, và hồ sơ lô giữ khả năng
> truy xuất. AI không được thay ba thứ đó.

---

<a id="phan-1-muc-2" name="phan-1-muc-2"></a>

### 2. Kiến trúc kỹ thuật: AI được đặt ở đâu

Phần này dành cho người đọc mã nguồn. Tài liệu tham chiếu đầy đủ là
[`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md); các mục liên quan trực tiếp
là §2.4 (design boundaries), §4.1–§4.7 (S1–S7), §8.4–§8.6 (provider exposure,
secrets, sandbox) và §9 (configuration).

#### 2.1 Ranh giới tất định

Pipeline chia làm hai loại tầng:

- **Tất định:** S1 (phân rã câu hỏi tất định; phần chủ đề do LLM là tùy chọn),
  S2 (điểm từ điển), S3 (ghi cache), S4 (engine số học), S5 (kiểm tra). Với
  cùng đầu vào, cùng cấu hình và cùng seed, các phần tất định này tạo ra cùng
  artifact. Phần chủ đề do LLM ở S1 (nếu bật) không nằm trong cam kết tái lập.
- **Không tất định:** S6 (`UniversalCoScientistPipeline`). Đây là tầng duy nhất
  bản chất là do LLM dẫn dắt. Sampling, thứ tự gọi và việc chọn model có thể
  làm kết quả khác giữa hai lần chạy, ngay cả khi S1–S5 giống hệt.

Hệ thống ghi nhãn rõ điều này:

- `QueryBreakdown.method` là `deterministic` hoặc `deterministic+llm`;
- kết quả phân loại có nguồn `classifier` và các điểm thành phần;
- nếu S6 lỗi hoặc quá thời gian, `orchestration = null` và một cảnh báo được
  thêm vào; kết quả S4 vẫn được giữ;
- có thể bỏ hẳn S6 bằng `--skip-orchestrator` hoặc cấu hình
  `orchestrator.enabled = false`.

Sự phân biệt này quan trọng: nó cho phép người đọc biết phần nào có thể tái
lập và phần nào chỉ là đầu ra của một lần sinh văn bản.

#### 2.2 Các bề mặt có AI

| Tầng | Dạng AI | Vai trò | Giới hạn |
| --- | --- | --- | --- |
| S1 — đọc bài trực tuyến | LLM sinh JSON có schema | Trích nhận định, chỉ số, chủ đề từ bài báo | Mỗi `key_claim` phải có `evidence_quote` dài tối thiểu 15 ký tự và xuất hiện nguyên văn trong bài; nếu không, claim bị loại. Có một lần thử lại để sửa; nếu vẫn không có trích dẫn, hệ thống dùng câu trích đầu tiên đủ dài và ghi cờ `deterministic_evidence_fallback`. |
| S1 — phân rã câu hỏi | LLM tùy chọn | Thêm chủ đề và cụm từ tìm kiếm | Không được thêm tham số số học. Mọi giá trị số trong phản hồi LLM bị bỏ. |
| S2 — classifier | Mô hình embedding | So sánh câu hỏi với prototype vĩ mô/vi mô | Chỉ là gợi ý; trọng số mặc định 0.55 từ điển / 0.45 embedding. Nếu embedding lỗi, quay về điểm từ điển. Người dùng chọn chế độ thì lựa chọn đó thắng. |
| S5 — re-validation | Embedding, có fallback từ vựng | Đo mức tương đồng giữa nhận định và nguồn | Các quyết định đạt/không đạt là luật tất định; embedding chỉ cung cấp điểm tương đồng. |
| S6 — truy xuất | Embedding + ChromaDB | Contrastive filtering, MMR theo thời gian, CRAG | Ngưỡng lấy từ cấu hình; không do LLM tự khai. |
| S6 — điều phối | LLM sinh văn bản | Sinh giả thuyết, phản biện, chấm điểm, viết mã mô phỏng | Không được tự đánh dấu `verified_*`; không được bỏ qua hộp cát; không được thay S5. |

#### 2.3 Những gì LLM không được phép làm

- **Không tạo hoặc sửa con số kinh tế.** S4 là nguồn duy nhất của kết quả kinh
  tế được báo cáo. LLM có thể diễn đạt lại kết quả hoặc viết mã mô phỏng để kiểm
  tra giả thuyết, nhưng không được tính hộ con số kinh tế.
- **Không thêm tham số số học vào S1.** Giá trị số trong phản hồi LLM bị loại
  bỏ; chỉ chủ đề và cụm từ tìm kiếm được giữ.
- **Không tự phong chứng nhận.** `sanitize_and_attest_facts()` xóa mọi vị từ
  `verified_*` không có receipt HMAC hợp lệ. Một câu "đã kiểm chứng" do LLM viết
  không có giá trị.
- **Không vượt qua bằng chứng.** Claim không có trích dẫn nguyên văn sẽ bị loại
  khỏi bài trực tuyến; macro claim phải tham chiếu artifact SHA-256.
- **Không tự lưu dữ liệu cá nhân.** Bối cảnh cá nhân chỉ được lưu khi
  `persist_opt_in=true`; mặc định chỉ nằm trong bộ nhớ tiến trình.
- **Không nhìn thấy API key.** Key được đọc vào bộ nhớ, không vào config, cache,
  log, audit hay database; mọi thông điệp lỗi đi ra đều qua `redact_secrets`.

#### 2.4 Cơ chế kiểm soát

**Hợp đồng kiểu dữ liệu.** Mỗi tầng có contract Pydantic trong
`app/pipeline_v2/contracts.py`. Đầu ra sai schema bị từ chối, không được "đoán
tiếp".

**Bằng chứng và truy xuất.** Macro artifact được chuẩn hóa JSON theo RFC 8785
và băm SHA-256; mỗi macro claim mang tham chiếu `artifact:sha256:...`. Với bài
trực tuyến, trích dẫn phải xuất hiện nguyên văn trong văn bản bài. Thiếu dữ liệu
thì ghi `not_estimated`; panel vĩ mô thiếu thì dừng chạy, không thay bằng panel
tổng hợp.

**Hộp cát và chứng nhận.** S6 biến giả thuyết thành một script Python chạy trong
tiến trình con:

- thư mục riêng dưới `/tmp/sandbox/{task_id}/{hypothesis_id}/`;
- môi trường tối thiểu, chỉ có `PATH` và `PYTHONIOENCODING`, không kế thừa API
  key;
- timeout 8.0 giây;
- chỉ được chứng nhận khi exit code bằng 0 **và** mọi metric nằm trong
  `MetricBoundSpec`;
- receipt là `HMAC-SHA256(secret, "<hypothesis_id>:universal_parameter_forcing:0:<digest>")`,
  với `digest = SHA256(stdout)[:16]`.

Cần nói rõ giới hạn: đây là cách ly tiến trình và thời gian, **không phải** ranh
giới bảo mật kiểu container. Code trong hộp cát vẫn đọc được file mà cùng người
dùng đọc được và có thể gọi mạng nếu máy chủ không chặn. Muốn chạy mã không tin
cậy, phải giới hạn mạng và quyền file ở tầng máy chủ/container.

**Logic xác suất và bộ nhớ phủ định.** Mỗi giả thuyết được quét bằng ProbLog
với ontology động, tập predicate được phép và luật xung đột. Điểm:

```text
Net_Fuzzy_Score = P(viable) * (1 - P(conflict))
```

Nếu `P(conflict) > 0.65` thì giả thuyết cần sửa; nếu điểm dưới `0.20` thì bị
loại. Xung đột được ghi vào bộ nhớ phủ định và biên dịch thành luật phạt cho
lần sau, với hệ số suy giảm `0.60`.

**Chọn model và tách vai.** Router key pool dùng UCB1 để chọn model theo vai
trò, với `UCB1_EXPLORATION_C = 1.0`. Judge không được dùng đúng model mà Worker
đang dùng, để giảm thiên lệch tự chấm. S6 có ít nhất bốn Worker ở các mức nhiệt
độ khác nhau và hai Judge mặc định.

**Router và dữ liệu rời máy.** Router hỗ trợ Ollama cục bộ hoặc endpoint
OpenAI-compatible (BYOK). Mặc định sinh văn bản và embedding chạy cục bộ. Dữ
liệu chỉ rời máy khi bật Cohere, cấu hình BYOK, hoặc bật lấy bài trực tuyến.
BYOK key đọc từ `.env` (đã gitignore) hoặc `--byok-stdin`, không nhận qua HTTP
request body.

**Suy giảm có kiểm soát.** Nếu S6 quá 2.700 giây hoặc lỗi, kết quả S4 vẫn được
giữ, `orchestration` thành `null`, và đầu ra là `ok_with_warnings`. Nếu S5
không đạt, bản ghi của lần chạy bị cách ly và không được purge; pipeline không
fallback sang số bịa.

#### 2.5 Vì sao không gọi thẳng API chatbot công cộng?

Một API chatbot công cộng chỉ nhận prompt và trả văn bản. Nó không tự chạy
engine vĩ mô/vi mô, không tạo artifact có băm, không có S5, và không có hộp cát
chứng nhận. Nếu tự xây một lớp gọi chatbot rồi coi đó là "hệ thống", bạn sẽ
thiếu:

1. **Thực thi số học có hợp đồng.** Không có `MacroResult`/`MicroResult` với
   schema, seed và artifact; con số chỉ là chữ trong câu trả lời.
2. **Truy xuất và kiểm chứng.** Không có `artifact:sha256:...`, không có trích
   dẫn nguyên văn, không có bản ghi bằng chứng.
3. **Fail-closed.** Không có quarantine, không có `not_estimated` bắt buộc,
   không có lý do để từ chối một câu trả lời thiếu căn cứ.
4. **Tái lập.** Không kiểm soát được phiên bản model, sampling hay thay đổi
   prompt phía nhà cung cấp.
5. **Ranh giới dữ liệu.** Hồ sơ hộ và văn bản xử lý rời máy theo điều khoản của
   bên thứ ba; không có cơ chế local-first.
6. **Điều khiển model.** Không có UCB1, không tách Worker/Judge, không có
   fallback cục bộ, không có redaction theo ngữ cảnh.
7. **Chứng nhận chạy thử.** Không phân biệt được "model nói đã kiểm chứng" với
   "mã đã chạy và nằm trong biên".

Điểm cuối là quan trọng nhất: **cùng một model có thể dùng làm nhà cung cấp
BYOK trong hệ thống này**, nhưng khi đó nó vẫn bị đặt sau S5, sau contract, sau
redaction và sau hộp cát. Giá trị không nằm ở thương hiệu model; giá trị nằm ở
những lớp kiểm soát xung quanh nó.

Chatbot công cộng vẫn là công cụ tốt để giải thích khái niệm, soạn câu hỏi,
hoặc học thuật ngữ. Nó không phải công cụ phù hợp để tạo một con số có nguồn
cho quyết định thật.

#### 2.6 Đọc mã ở đâu

| Chủ đề | Tệp |
| --- | --- |
| Contract và kiểu dữ liệu | `app/pipeline_v2/contracts.py` |
| Điều phối S1–S7 | `app/pipeline_v2/orchestrator.py` |
| Classifier | `app/pipeline_v2/classifier.py` |
| Re-validation | `app/pipeline_v2/revalidation.py` |
| Router BYOK | `app/pipeline_v2/byok_router.py` |
| Router JSON và key pool | `app/services/json_router.py`, `app/core/smart_key_router.py` |
| Embedding, ChromaDB, CRAG/MMR | `app/services/embedding_engine.py` |
| S6 orchestrator | `app/orchestrator/universal_pipeline.py` |
| Hộp cát và chứng nhận | `app/sandbox/runner.py`, `app/sandbox/attestation.py` |
| ProbLog | `app/logic_engine/` |
| Cấu hình | `config/pipeline_v2.json`, `config/classifier_calibration.json` |

Tài liệu liên quan:

- [`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md) — kiến trúc, công thức, hệ
  số, cấu hình, phụ lục và thuật ngữ.
- [`PIPELINE_V2_COMPLETION_REPORT.md`](PIPELINE_V2_COMPLETION_REPORT.md) — báo
  cáo triển khai và kiểm chứng.
- [`HUONG_DAN_PIPELINE_V2.md`](HUONG_DAN_PIPELINE_V2.md) — hướng dẫn vận hành
  bằng tiếng Việt.

#### 2.7 Giới hạn cần nhớ

- S6 không tất định; hai lần chạy có thể cho giả thuyết và cách diễn đạt khác
  nhau.
- Hộp cát là cách ly tiến trình, không phải container bảo mật.
- Khi triển khai, đặt `SOVEREIGN_SANDBOX_SECRET` mạnh; mã có giá trị fallback
  dùng cho phát triển.
- Classifier chỉ là gợi ý; nó không thay quyết định của người dùng.
- `redact_secrets()` là lọc theo mẫu, không phải hệ thống chống thất lạc dữ
  liệu toàn diện.
- Với dữ liệu hộ nhạy cảm, nên dùng Ollama cục bộ và để trống `COHERE_API_KEY`
  cùng BYOK.

---

<a id="phan-2" name="phan-2"></a>

## Part 2 — English

<a id="phan-2-muc-1" name="phan-2-muc-1"></a>

### 1. AI in the factory: a plain-language explanation

Imagine OSINTViet as a small factory.

- **Raw material** is your question and, if you provide one, a household profile:
  income, spending, assets, debt and household size.
- **Finished product** is a JSON report that states its sources, formulas,
  assumptions, warnings and validation result.

The factory has three kinds of "staff":

1. **Machines** — fixed pieces of calculation code. The same input and the same
   configuration produce the same result.
2. **The storeroom** — a place that keeps processed material under a batch
   number for each run.
3. **A new office assistant** — this is the AI. The assistant reads quickly,
   writes well, summarises, and suggests ideas. But the assistant is **not
   allowed to operate the calculator** in place of the machines, and **not
   allowed to sign the quality certificate** in place of the inspection
   station.

The line has seven stations:

| Station | What actually happens | Does AI do it? |
| --- | --- | --- |
| **S1 — Receiving** | Read the question, extract terms, record household numbers. In live-article mode, read an article and extract evidence. | Yes, but only to help read and suggest topics/search phrases. In query breakdown, any numeric value returned by the AI is discarded. For articles, every claim needs a verbatim quote, and a number is kept only when it is visible in the text. |
| **S2 — Sorting room** | Label the request as macro, micro or mixed. | Yes, but only as a **suggestion**. The lexicon count is deterministic; the "meaning" part uses a model that compares meaning. If you choose a mode yourself, your choice wins. |
| **S3 — Batch store** | Write processed material to the cache under a run ID. | No. |
| **S4 — Calculation floor** | Run the macro calculator (Vietnam data, shock scenarios, policy simulation) and the household calculator (spending, income, saving, poverty). | **No.** This is where the numbers are produced. AI may not replace the calculator or change the result. |
| **S5 — Inspection** | Check grounding, consistency, completeness and mode integrity. | Only meaning-based comparison where available; otherwise lexical overlap. The pass/fail decision is a deterministic rule. |
| **S6 — Research room** | Read material, propose hypotheses, critique them, score them, and run numerical bounds in a separate test environment (sandbox). | **Yes — this is where AI does most of its work.** But it is an ideas room, not the line that produces the numbers. |
| **S7 — Packing** | Emit the final JSON without raw cache content or personal data. | No. |

**An example.** You ask: "Food prices rise 20%; how is my household affected?"
The factory takes your household numbers, runs the household spending calculator
(price, income and saving model), and returns the transmission channels. AI may
help read the question, suggest wording, and propose a mechanism. But the final
number comes from a machine or from a cited source. If data is missing, the
report says `not_estimated` — "not enough information", not zero and not a
plausible guess.

#### Why not give the question straight to ChatGPT or Gemini?

Imagine phoning an outside consultant, describing the factory over the phone,
and receiving a very fluent written report. That report can help you understand
a concept, but:

- **No machine was run.** The consultant does not operate the calculation floor.
  They write from patterns and experience, so a number may only sound plausible.
- **There is no batch record.** Nobody knows which data, formula or assumption
  produced the report. You cannot replay it to check.
- **There is no inspection station.** Nothing forces the answer to refuse when
  evidence is missing. There is no `not_estimated`, no quarantine for a failed
  run, and no execution certificate.
- **There is no data boundary.** Your income, debt and spending profile is sent
  to a third party under their terms. They may store or use it according to
  their policy.
- **There is no version control.** The provider can change the model without
  telling you. The same question can produce a different answer, and you have no
  record explaining why.

In short: a public chatbot is a good report writer. It is not the factory, not
the calculator, and not the person who signs the certificate. It is reasonable
for **understanding a concept**; it is the wrong tool for **getting a number to
act on**.

> **In one sentence:** AI in OSINTViet is the office assistant and the idea
> generator. The machines do the arithmetic, the inspection station guards
> quality, and the batch record preserves provenance. AI is not allowed to
> replace those three.

---

<a id="phan-2-muc-2" name="phan-2-muc-2"></a>

### 2. Technical architecture: where AI sits

This part is for people reading the source. The full reference is
[`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md); the directly relevant
sections are §2.4 (design boundaries), §4.1–§4.7 (S1–S7), §8.4–§8.6 (provider
exposure, secrets, sandbox) and §9 (configuration).

#### 2.1 The determinism boundary

The pipeline has two kinds of stages:

- **Deterministic:** S1 (deterministic query breakdown; LLM topics are
  optional), S2 (lexicon score), S3 (cache write), S4 (numerical engines), S5
  (checks). With the same input, configuration and seed, these deterministic
  parts produce the same artifacts. The optional LLM topics in S1 are outside
  that reproducibility guarantee.
- **Non-deterministic:** S6 (`UniversalCoScientistPipeline`). This is the only
  stage that is inherently LLM-driven. Sampling, call order and model selection
  can change the output between runs even when S1–S5 are identical.

The system labels this boundary:

- `QueryBreakdown.method` is `deterministic` or `deterministic+llm`;
- the classifier result records its source and component scores;
- if S6 fails or times out, `orchestration = null` and a warning is added; the
  S4 result is preserved;
- S6 can be skipped entirely with `--skip-orchestrator` or
  `orchestrator.enabled = false`.

The distinction matters: a reader can tell which part is reproducible and which
part is the output of one text-generation run.

#### 2.2 AI surfaces

| Stage | Form of AI | Role | Limit |
| --- | --- | --- | --- |
| S1 — live article reading | Schema-constrained LLM | Extract claims, indicators and topics from an article | Every `key_claim` needs an `evidence_quote` of at least 15 characters that appears verbatim in the article; otherwise the claim is dropped. A correction retry is made; if no quote survives, the system uses the first sufficiently long article quote and records `deterministic_evidence_fallback`. |
| S1 — query breakdown | Optional LLM | Add topics and search phrases | It may not add numeric parameters. Any numeric value in the LLM response is discarded. |
| S2 — classifier | Embedding model | Compare the query with macro/micro prototypes | Advisory only; default weights are 0.55 lexicon / 0.45 embedding. If embeddings fail, the lexicon score is used. A user-selected mode wins. |
| S5 — re-validation | Embeddings, with lexical fallback | Measure similarity between claims and sources | Pass/fail remains a deterministic rule; embeddings only provide a similarity score. |
| S6 — retrieval | Embeddings + ChromaDB | Contrastive filtering, temporal MMR, CRAG | Thresholds come from configuration, not from LLM self-reporting. |
| S6 — orchestration | Generative LLM | Generate hypotheses, critique, score, write simulation code | It may not mark `verified_*` facts itself, bypass the sandbox, or replace S5. |

#### 2.3 What the LLM is not allowed to do

- **Create or repair economic numbers.** S4 is the only source of the reported
  economic results. The LLM may rephrase a result or write simulation code to
  test a hypothesis, but it may not calculate an economic result in its place.
- **Add numeric parameters in S1.** Numeric values in the LLM response are
  removed; only topics and search phrases are kept.
- **Certify its own work.** `sanitize_and_attest_facts()` strips every
  `verified_*` predicate that does not carry a valid HMAC receipt. A sentence
  saying "verified" has no value on its own.
- **Bypass evidence.** A live-article claim without a verbatim quote is dropped;
  a macro claim must reference the SHA-256 artifact.
- **Persist personal data.** Personal context is stored only when
  `persist_opt_in=true`; by default it stays in process memory.
- **See API keys.** Keys are read into memory, not into config, cache, logs,
  audit rows or the database; outgoing error text passes through
  `redact_secrets`.

#### 2.4 Controls

**Typed contracts.** Every stage has a Pydantic contract in
`app/pipeline_v2/contracts.py`. Output that does not match the schema is
rejected rather than "guessed forward".

**Evidence and provenance.** The macro artifact is canonicalised with RFC
8785-compatible JSON and hashed with SHA-256; every macro claim carries an
`artifact:sha256:...` support reference. For live articles, the quote must
appear verbatim in the article text. Missing data becomes `not_estimated`; a
missing macro panel stops the run rather than being replaced by a synthetic
panel.

**Sandbox and attestation.** S6 turns a hypothesis into a Python script that
runs in a child process:

- a dedicated directory under `/tmp/sandbox/{task_id}/{hypothesis_id}/`;
- a minimal environment containing only `PATH` and `PYTHONIOENCODING`, with no
  inherited API keys;
- an 8.0-second timeout;
- attestation only when the exit code is zero **and** every metric lies inside
  its `MetricBoundSpec`;
- the receipt is `HMAC-SHA256(secret, "<hypothesis_id>:universal_parameter_forcing:0:<digest>")`,
  where `digest = SHA256(stdout)[:16]`.

The limitation must be stated plainly: this is process and time isolation, not
a container-grade security boundary. Code in the sandbox can still read files
accessible to the same user and can make network calls unless the host blocks
them. Running untrusted code safely requires host- or container-level network
and filesystem restrictions.

**Probabilistic logic and negative memory.** Each hypothesis is scanned with
ProbLog against a dynamic ontology, an allowed predicate set and conflict
rules. The score is:

```text
Net_Fuzzy_Score = P(viable) * (1 - P(conflict))
```

If `P(conflict) > 0.65`, the hypothesis needs repair; if the score is below
`0.20`, it is rejected. Conflicts are written to negative memory and compiled
into penalty rules for later runs, with a decay factor of `0.60`.

**Model selection and role separation.** The key-pool router uses UCB1 to select
a model per role, with `UCB1_EXPLORATION_C = 1.0`. A Judge may not use the same
model that a Worker is currently using, which reduces self-preference bias. S6
uses at least four Workers at different temperatures and two Judges by default.

**Router and data egress.** The router supports local Ollama or an
OpenAI-compatible BYOK endpoint. Generation and embeddings are local by default.
Data leaves the machine only when Cohere is enabled, a BYOK endpoint is
configured, or live article fetching is enabled. A BYOK key is read from
`.env` (gitignored) or `--byok-stdin`; the HTTP API never accepts a key in the
request body.

**Controlled degradation.** If S6 exceeds 2,700 seconds or fails, the S4 result
is kept, `orchestration` becomes `null`, and the output is `ok_with_warnings`.
If S5 fails, the run's cache entries are quarantined and not purged; the
pipeline does not fall back to invented numbers.

#### 2.5 Why not call a public chatbot API directly?

A public chatbot API accepts a prompt and returns text. It does not run the
macro or household engines, it does not produce a hashed artifact, it has no S5,
and it has no attested sandbox. If you build a thin wrapper around a chatbot and
call it "the system", you are missing:

1. **Contractual numerical execution.** There is no `MacroResult`/`MicroResult`
   with a schema, seed and artifact; numbers are just words in an answer.
2. **Provenance and verification.** There is no `artifact:sha256:...`, no
   verbatim quote check, and no evidence record.
3. **Fail-closed behaviour.** There is no quarantine, no mandatory
   `not_estimated`, and no reason for the system to refuse an unsupported
   answer.
4. **Reproducibility.** You do not control the model version, sampling, or
   prompt changes on the provider side.
5. **A data boundary.** Household profiles and processed text leave the machine
   under a third party's terms; there is no local-first mode.
6. **Model control.** There is no UCB1 routing, no Worker/Judge separation, no
   local fallback, and no context-aware redaction.
7. **Execution attestation.** There is no way to distinguish "the model said it
   was verified" from "code ran and stayed inside its bounds".

The last point is the most important: **the same model can be used as a BYOK
provider inside this system**, but it still sits behind S5, the contracts,
redaction and the sandbox. The value is not the model brand; it is the control
layers around it.

A public chatbot is still a good tool for explaining a concept, drafting a
question, or learning terminology. It is not the right tool for producing a
number with provenance for a real decision.

#### 2.6 Where to read the code

| Concern | File |
| --- | --- |
| Contracts and types | `app/pipeline_v2/contracts.py` |
| S1–S7 orchestration | `app/pipeline_v2/orchestrator.py` |
| Classifier | `app/pipeline_v2/classifier.py` |
| Re-validation | `app/pipeline_v2/revalidation.py` |
| BYOK router | `app/pipeline_v2/byok_router.py` |
| JSON router and key pool | `app/services/json_router.py`, `app/core/smart_key_router.py` |
| Embeddings, ChromaDB, CRAG/MMR | `app/services/embedding_engine.py` |
| S6 orchestrator | `app/orchestrator/universal_pipeline.py` |
| Sandbox and attestation | `app/sandbox/runner.py`, `app/sandbox/attestation.py` |
| ProbLog | `app/logic_engine/` |
| Configuration | `config/pipeline_v2.json`, `config/classifier_calibration.json` |

Related documents:

- [`TECHNICAL_REFERENCE.md`](TECHNICAL_REFERENCE.md) — architecture, formulas,
  coefficients, configuration, appendices and glossary.
- [`PIPELINE_V2_COMPLETION_REPORT.md`](PIPELINE_V2_COMPLETION_REPORT.md) —
  implementation and verification report.
- [`HUONG_DAN_PIPELINE_V2.md`](HUONG_DAN_PIPELINE_V2.md) — Vietnamese operating
  guide.

#### 2.7 Limits to keep in mind

- S6 is non-deterministic; two runs can produce different hypotheses and
  wording.
- The sandbox is process isolation, not a security container.
- In deployment, set a strong `SOVEREIGN_SANDBOX_SECRET`; the code has a
  development fallback.
- The classifier is advisory; it does not replace the user's choice.
- `redact_secrets()` is pattern-based filtering, not a complete
  data-loss-prevention system.
- For sensitive household data, use local Ollama and leave `COHERE_API_KEY`
  and the BYOK key empty.
