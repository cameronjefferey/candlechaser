from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Alpaca (news websocket)
    alpaca_key_id: str
    alpaca_secret_key: str

    # Anthropic (headline classification)
    anthropic_api_key: str
    anthropic_model: str = "claude-haiku-4-5"

    # Telegram (alert delivery)
    telegram_bot_token: str
    telegram_chat_id: str

    # Sources (later phases flip these on)
    enable_news: bool = True
    enable_filings: bool = False
    enable_halts: bool = False
    enable_options: bool = False

    # SEC EDGAR (filings source). SEC requires a real contact in the UA.
    sec_user_agent: str = "candlechaser/1.0 contact@example.com"
    edgar_poll_seconds: int = 10
    cluster_buy_window_days: int = 5      # trading days
    cluster_buy_min_insiders: int = 2
    activist_score: int = 85
    offering_score: int = 70
    cluster_buy_score: int = 75

    # Halts source
    halts_poll_seconds: int = 5
    halt_score: int = 80

    # Options flow source (Phase 4, off by default)
    polygon_api_key: str = ""

    # Behavior
    alert_score_threshold: int = 70
    ticker_cooldown_minutes: int = 15
    market_hours_only: bool = True
    # 04:00 = premarket open, 20:00 = after-hours close. Catches evening
    # earnings and premarket movers you can trade at the open.
    alert_start_et: str = "04:00"
    alert_end_et: str = "20:00"
    db_path: str = "./candlechaser.db"
    port: int = 10000  # Render injects PORT for web services


settings = Settings()
