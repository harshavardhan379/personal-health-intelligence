from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Personal Health Intelligence API"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]
    storage_dir: str = "storage"

    # LLM provider selection (YGH-83). "openai" | "grok" | "ollama".
    llm_provider: str = "ollama"
    llm_timeout_seconds: float = 30.0

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    xai_api_key: str = ""
    xai_model: str = "grok-2-latest"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"


settings = Settings()
