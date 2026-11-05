from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    tz: str = "Europe/Minsk"
    games_per_run: int = 20
    max_browse_pages_per_run: int = 3
    max_platform_pages: int = 5
    request_delay_sec: float = 1.5
    request_retries: int = 3
    scraper_use_fixtures: bool = False
    scraper_force_playwright: bool = False
    fixtures_dir: str = "tests/fixtures"
    run_scheduler: bool = True
    schedule_minutes: int = 60
    admin_token: str = ""
    llm_provider: str = "auto"  # auto|groq|gemini|openai|anthropic|mock
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    llm_logs_dir: str = "data/llm_logs"
    youtube_api_key: str = ""
    enable_youtube: bool = False


settings = Settings()
