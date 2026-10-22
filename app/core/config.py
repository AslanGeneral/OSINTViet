from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    PROJECT_NAME: str = "OSINT Macro-Micro Platform"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./osint_macro.db"
    
    # Security / Admin
    ADMIN_PASSCODE: str = "vro_change_this_secret_passcode_123"
    
    # LLM Router Config
    LLM_PROVIDER: Literal["local_ollama", "openai_compatible"] = "local_ollama"
    # Địa chỉ mặc định của Ollama trên Linux Mint:
    LOCAL_OLLAMA_URL: str = "http://127.0.0.1:11434"
    LOCAL_MODEL_NAME: str = "llama3:latest" # Hoặc qwen2.5:7b
    
    # Fallback / Cloud API (nếu cần)
    CLOUD_API_KEY: str = ""
    CLOUD_API_BASE_URL: str = "https://api.openai.com/v1"
    CLOUD_MODEL_NAME: str = "gpt-4o-mini"
    
    # Cache settings
    ENABLE_LLM_CACHE: bool = True
    CACHE_DIR: str = "./cache/llm_responses"

    # ==== [DT §6.1.1→§11] Embedding v5 Pro + Vector Store ====
    # Lưu ý: COHERE_API_KEY đọc trực tiếp qua os.getenv trong embedding_engine
    # theo đúng code §11; EMBED_MODEL cũng vậy. Các setting dưới đây dùng cho
    # Fallback Ollama và ChromaDB — giữ nguyên collection osint_macro_knowledge.
    EMBEDDING_MODEL: str = "embeddinggemma-2"  # model Ollama Fallback
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"

    # ==== [DT §6.1.3 - Appendix A hàng 4] Ngưỡng CRAG Tri-State ====
    # Quyết định của user: dùng "toán tử tượng trưng" nên 2 ngưỡng này đã
    # được đưa vào config để chỉnh qua .env — KHÔNG cứng hóa trong logic engine.
    # Giá trị mặc định dưới đây lấy theo code §11; nếu muốn dùng bộ số
    # của DT mô tả (0.65/0.42) chỉ cần nhập vào .env:
    #   CRAG_CORRECT_THRESHOLD=0.65 CRAG_AMBIGUOUS_THRESHOLD=0.42
    CRAG_CORRECT_THRESHOLD: float = 0.62
    CRAG_AMBIGUOUS_THRESHOLD: float = 0.40

    # ==== [DT §6.1.3 - Appendix A hàng 7/8] MMR + Decompose-Recompose ====
    # beta tương ứng lambda_mmr=0.70 của code §11 (quyết định hàng 7 của user);
    # gamma là hệ số giảm thời gian e^{-gamma*delta_t} (ngày). User đã chốt
    # "time decay = allowed": bật decay với gamma mặc định 0.01/ngày
    # (spec chỉ có ký hiệu gamma, không có số), có thể override qua .env.
    MMR_BETA: float = 0.70
    MMR_TIME_DECAY_GAMMA: float = 0.01
    # Ngưỡng giữ câu trong Decompose-Recompose (AMBIGUOUS) — hệ số cấu hình
    DECOMPOSE_KEEP_THRESHOLD: float = 0.62

    # ==== [DT §6.1.3 - Appendix A hàng 9] Numerical Divergence Detector ====
    # Quyết định của user: CV > 0.35, trích cả 4 nhóm unit [%, tỷ, VND, USD]
    NUMERICAL_CV_THRESHOLD: float = 0.35

    # ==== [DT §6.5] Negative Memory Compiler ====
    # gamma_decay = 0.6 theo đúng đặc tả §6.5.3 (không phải giá trị tùy chọn).
    NEGATIVE_MEMORY_DECAY_FACTOR: float = 0.60

    # ==== [DT §6.2] Key Pool ====
    # keys_pool.json nằm trong .gitignore; nếu không có file thì đọc
    # GEMINI_API_KEYS (danh sách phẩy cách) + EXTRA_API_KEYS.
    GEMINI_API_KEYS: str = ""
    EXTRA_API_KEYS: str = ""
    # Hệ số thăm dò c của công thức UCB1 (tượng trưng — không nguồn nào cho số)
    UCB1_EXPLORATION_C: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()