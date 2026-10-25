"""Settings, one set of variables for three environments (YGH-104).

    development  the default: local Postgres and Ollama, dev-only secrets, API docs on.
    test         the automated tests: same defaults, so nothing is needed to run them.
    production   refuses to start with a missing or dev-only secret (see `production_problems`),
                 and serves no API docs.

Every value comes from an environment variable of the same name in capitals (CORS_ORIGINS,
JWT_SECRET, ...) or from a `.env` file; `.env.example` lists them.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me"
DEV_DATABASE_URL = "postgresql+psycopg://phi:phi_dev_password@localhost:5433/phi"
ENVIRONMENTS = ("development", "test", "production")
MIN_JWT_SECRET_CHARS = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Personal Health Intelligence API"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]
    storage_dir: str = "storage"
    database_url: str = DEV_DATABASE_URL

    jwt_secret: str = DEV_JWT_SECRET
    jwt_expire_minutes: int = 60 * 24

    # LLM provider selection (YGH-83). "openai" | "grok" | "ollama".
    llm_provider: str = "ollama"
    llm_timeout_seconds: float = 90.0  # a cold local model load can exceed 30s

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    xai_api_key: str = ""
    xai_model: str = "grok-2-latest"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def production_problems(self) -> list[str]:
        """What is wrong with these settings for a production run, as plain sentences naming the
        variable and never its value (the output goes to a log)."""
        problems = []
        if self.environment not in ENVIRONMENTS:
            problems.append(f"ENVIRONMENT must be one of {', '.join(ENVIRONMENTS)}")
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("JWT_SECRET is the development default; set a long random secret")
        elif len(self.jwt_secret) < MIN_JWT_SECRET_CHARS:
            problems.append(f"JWT_SECRET must be at least {MIN_JWT_SECRET_CHARS} characters")
        if "phi_dev_password" in self.database_url:
            problems.append("DATABASE_URL uses the development database password")
        if not self.cors_origins:
            problems.append("CORS_ORIGINS is empty, so the web app could not call the API")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must list the web app's origin, not '*'")
        if self.storage_dir.startswith(("storage", ".")) or not self.storage_dir.startswith("/"):
            problems.append("STORAGE_DIR must be an absolute path on a persistent volume")
        provider = self.llm_provider.lower()
        if provider == "openai" and not self.openai_api_key:
            problems.append("OPENAI_API_KEY is required when LLM_PROVIDER is openai")
        if provider == "grok" and not self.xai_api_key:
            problems.append("XAI_API_KEY is required when LLM_PROVIDER is grok")
        return problems


settings = Settings()
