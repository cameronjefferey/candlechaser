from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Alpaca (news websocket)
    alpaca_key_id: str
    alpaca_secret_key: str

    # OpenAI (headline classification)
    openai_api_key: str
    openai_model: str = "gpt-5.4-mini"

    # Telegram (alert delivery)
    telegram_bot_token: str
    telegram_chat_id: str

    # Behavior
    alert_score_threshold: int = 70
    ticker_cooldown_minutes: int = 15
    market_hours_only: bool = True
    alert_start_et: str = "07:00"
    alert_end_et: str = "16:00"
    db_path: str = "./candlechaser.db"


settings = Settings()
