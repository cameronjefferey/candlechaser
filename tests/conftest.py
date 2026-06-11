import os

# Hermetic tests: satisfy required settings before any app module imports config.
for key in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(key, "test")
