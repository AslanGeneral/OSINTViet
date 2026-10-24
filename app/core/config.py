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
    # The live workflow constrains this model with JSON schemas.  On the
    # reference machine llama3.2:3b is the largest model that fits in RAM.
    LOCAL_MODEL_NAME: str = "llama3.2:3b"
    # Keep one model slot and one request slot for the 7.6 GB reference host.
    # Loading the 3B LLM and the embedding model at the same time, with a
    # 6144-token context, previously triggered the kernel OOM killer.
    LOCAL_LLM_CONCURRENCY: int = 1
    LOCAL_LLM_NUM_CTX: int = 4096
    LOCAL_LLM_TIMEOUT_SECONDS: float = 600.0
    
    # Fallback / Cloud API (nếu cần)
    CLOUD_API_KEY: str = ""
    CLOUD_API_BASE_URL: str = "https://api.openai.com/v1"
    CLOUD_MODEL_NAME: str = "gpt-4o-mini"
    
    # Cache settings
    ENABLE_LLM_CACHE: bool = True
    CACHE_DIR: str = "./cache/llm_responses"

    # ==== Embedding v2.0: dual provider + vector store ====
    # auto = Cohere nếu có COHERE_API_KEY, ngược lại dùng Ollama local.
    EMBEDDING_PROVIDER: Literal["auto", "cohere", "embeddinggemma"] = "auto"
    COHERE_API_KEY: str = ""
    # Giữ EMBED_MODEL để tương thích .env cũ; COHERE_EMBED_MODEL là tên chuẩn.
    EMBED_MODEL: str = ""
    COHERE_EMBED_MODEL: str = "embed-v5.0-pro"
    COHERE_EMBED_ENDPOINT: str = "https://api.cohere.com/v2/embed"
    COHERE_EMBED_BATCH_SIZE: int = 96
    EMBEDDING_MODEL: str = "embeddinggemma-2:740m"
    EMBEDDING_DIMENSION: int = 256
    EMBEDDING_HTTP_TIMEOUT_SECONDS: float = 25.0
    # Optional domain statement used to build dynamic anchors without touching
    # the orchestrator pipeline. Leave empty to use the generic anchors below.
    EMBEDDING_DOMAIN_STATEMENT: str = ""
    EMBEDDING_POSITIVE_ANCHOR: str = (
        "Bằng chứng có nguồn kiểm chứng, số liệu thống kê, phân tích chính sách "
        "và tác động kinh tế - xã hội."
    )
    EMBEDDING_NEGATIVE_ANCHOR: str = (
        "Quảng cáo, khuyến mãi, bài PR, tin đồn không nguồn, nội dung giật gân."
    )
    INGESTION_CONTRASTIVE_THRESHOLD: float = 0.42
    CONTRASTIVE_NEGATIVE_MARGIN: float = 0.30
    CONTRASTIVE_NEGATIVE_WEIGHT: float = 0.50
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"
    CHROMA_COLLECTION_NAME: str = "osint_macro_knowledge"

    # ==== [DT §6.1.3 - Appendix A hàng 4] Ngưỡng CRAG Tri-State ====
    # Hai ngưỡng nằm trong config để chỉnh qua .env, không hardcode trong engine.
    CRAG_CORRECT_THRESHOLD: float = 0.62
    CRAG_AMBIGUOUS_THRESHOLD: float = 0.40

    # ==== [DT §6.1.3 - Appendix A hàng 7/8] MMR + Decompose-Recompose ====
    # Task v2.0 chốt lambda_mmr=0.70, gamma=0.005/ngày, giữ câu >= 0.48.
    MMR_BETA: float = 0.70
    MMR_TIME_DECAY_GAMMA: float = 0.005
    DECOMPOSE_KEEP_THRESHOLD: float = 0.48

    # ==== [DT §6.1.3 - Appendix A hàng 9] Numerical Divergence Detector ====
    # CV > 0.35, trích các nhóm đơn vị [%, tỷ, nghìn tỷ, VND, USD].
    NUMERICAL_CV_THRESHOLD: float = 0.35

    # ==== Proximity Agent + sample ingestor ====
    PROXIMITY_SIMILARITY_THRESHOLD: float = 0.86
    MACRO_NEWS_SOURCES: str = ""
    MACRO_NEWS_MAX_ARTICLES: int = 10

    # ==== [DT §6.5] Negative Memory Compiler ====
    # gamma_decay = 0.6 theo đúng đặc tả §6.5.3 (không phải giá trị tùy chọn).
    NEGATIVE_MEMORY_DECAY_FACTOR: float = 0.60

    # ==== Full-circle live workflow ====
    # The workflow writes JSON artifacts outside the repository so runs never
    # become accidental fixtures.  SQLite/Chroma remain the canonical stores.
    WORKFLOW_ARTIFACT_DIR: str = "/tmp/osint_workflow"
    WORKFLOW_ECON_FETCH_SOURCES: str = (
        "https://vnexpress.net/kinh-doanh,"
        "https://vneconomy.vn/tieu-diem.htm"
    )
    WORKFLOW_ECON_FETCH_MAX_ATTEMPTS: int = 3
    WORKFLOW_ECON_FETCH_ARTICLE_ATTEMPTS: int = 3
    WORKFLOW_WORD_FILTER_MIN_TERMS: int = 3
    WORKFLOW_WORD_FILTER_MIN_SCORE: float = 0.35
    WORKFLOW_WORD_FILTER_MIN_ANALYSIS_RELEVANCE: float = 0.45
    WORKFLOW_WORLD_BANK_BASE_URL: str = "https://api.worldbank.org/v2"
    WORKFLOW_WORLD_BANK_COUNTRY: str = "VNM"
    WORKFLOW_ARIMA_FORECAST_STEPS: int = 6
    WORKFLOW_MIN_STATS_POINTS: int = 30

    # ==== [DT §6.2] Key Pool ====
    # keys_pool.json nằm trong .gitignore; nếu không có file thì đọc
    # GEMINI_API_KEYS (danh sách phẩy cách) + EXTRA_API_KEYS.
    GEMINI_API_KEYS: str = ""
    EXTRA_API_KEYS: str = ""
    # Hệ số thăm dò c của công thức UCB1 (tượng trưng — không nguồn nào cho số)
    UCB1_EXPLORATION_C: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()