from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/storyforge"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret_key: str = ""
    jwt_expire_hours: int = 24

    # LLM configuration
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-v4-flash"
    llm_models: str = ""
    llm_fallback_models: str = ""
    llm_proxy: str = ""
    llm_max_sys_chars: int = 50000
    llm_max_rag_chars: int = 10000
    llm_context_window: int = 120000  # DeepSeek 128K context window (tokens)

    # CORS configuration
    cors_origins: list[str] = ["http://localhost:5173"]

    # Embedding configuration
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_proxy: str = ""

    # ── Web Search ────────────────────────────────────────────────────
    # SearXNG instance URL (Docker service name)
    searxng_url: str = "http://searxng:8080"
    # Search result size limits
    search_max_results: int = 10
    search_min_snippet_len: int = 20
    # SerpAPI key (optional upgrade)
    serpapi_api_key: str = ""
    # Fallback to DuckDuckGo if SearXNG unavailable
    search_enable_ddg_fallback: bool = True

    # ── Web Fetch ─────────────────────────────────────────────────────
    # Max content size in chars fetched from a URL
    web_fetch_max_chars: int = 50000
    # Max URL fetch timeout in seconds
    web_fetch_timeout: int = 15
    # Cache TTL in seconds
    web_fetch_cache_ttl: int = 900  # 15 minutes
    # Max cache entries
    web_fetch_cache_max: int = 64


settings = Settings()
