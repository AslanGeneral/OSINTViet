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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()