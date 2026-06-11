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
