from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/storyforge"
    redis_url: str = "redis://redis:6379/0"

    # LLM configuration
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-v4-flash"
    llm_models: str = ""
    llm_fallback_models: str = ""
    # Model used by the middle-LLM tool-result compressor/cleaner
    # (app.agent.middle_compress). Empty → falls back to llm_model.
    # Should be a fast/cheap model — its context is always clean.
    llm_middle_model: str = ""
    llm_proxy: str = ""
    llm_max_sys_chars: int = 50000
    llm_max_rag_chars: int = 10000
    llm_context_window: int = 400000  # DeepSeek V4 1M context window (tokens)

    # ── Consistency analysis engine (v2) ────────────────────────────────
    # Scene content is processed in bounded blocks so arbitrarily large
    # projects never blow the context window. These are tuning knobs that
    # may be recalibrated after load testing (see 一致性分析引擎v2设计文档).
    consistency_max_concurrency: int = 16       # global LLM call budget
    consistency_block_chars: int = 6000         # extraction block size (chars)
    consistency_skip_small_scene_chars: int = 500  # scenes below this skip content extraction
    consistency_max_tokens: int = 8192          # hard gate (shares budget w/ reasoning)
    consistency_merge_max_tokens: int = 4096
    consistency_verify_max_tokens: int = 4096
    consistency_global_max_tokens: int = 8192
    consistency_verify_batch: int = 3           # conflict candidates per verify call
    consistency_merge_batch_chapters: int = 10  # chapters merged per LLM call
    consistency_global_budget_ratio: float = 0.25  # fraction of context window for global stage

    # ── Consistency analysis engine (v3 ledger) ─────────────────────────
    # Write-time fact ledger knobs (一致性分析引擎v3设计文档 §14.4).
    consistency_audit_interval_s: int = 60          # periodic hash audit interval
    consistency_cluster_cap: int = 32               # attribute cluster distinct-value guardrail
    consistency_judge_batch: int = 5                # judge batch size (was verify_batch=3)
    consistency_judge_timeout_s: int = 60
    consistency_extract_timeout_s: int = 90
    consistency_evidence_chars: int = 80            # max evidence quote length
    consistency_setting_context_chars: int = 800    # judge setting-context cap
    consistency_role_list_n: int = 50               # role-list truncation in extractor
    consistency_global_projection_cap: int = 7000   # global projection chars; split above this

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

