# Open Plugin Folder [AR §7.1]

Thả file `.py` vào đúng thư mục con; `load_plugins()` sẽ import tự động,
decorator `@hub.register_...` tự đăng ký vào `OpenSystemRegistry`.

| Thư mục | Decorator | Chữ ký thống nhất |
|---|---|---|
| `ingestors/` | `@hub.register_ingestor("name")` | `async def fn(query: str) -> dict | list` (dict dạng `{"source":..., "raw_data":[...]}` được chấp nhận) |
| `embeddings/` | `@hub.register_embedding("name")` | `async def fn(texts, input_type) -> list` |
| `critics_practical/` | `@hub.register_practical_tool("name")` | `async def fn(claim: str, **kwargs) -> dict` |
| `critics_theoretical/` | `@hub.register_theory_algo("name")` | `async def fn(parameters: dict, **kwargs) -> dict` |
| `verifiers/` | `@hub.register_verifier("name")` | `fn(hypothesis_id, facts, ontology=None) -> dict` |
| `sandboxes/` | `@hub.register_sandbox("name")` | `async def fn(hypothesis_json: dict, hard_limits: dict) -> dict` |

Quy ước ngôn ngữ [NN §8]: code/khóa JSON/predicate 100% English snake_case;
nội dung, truy vấn, giải thích 100% tiếng Việt; comment tiếng Việt.
