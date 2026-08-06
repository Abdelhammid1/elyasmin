import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    DB_PATH = BASE_DIR / "instance" / "farm.db"
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{DB_PATH}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.getenv("SESSION_LIFETIME_MINUTES", "30"))
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
    LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "60"))

    LANGUAGE = "ar"
    TIMEZONE = "Africa/Cairo"

    # ---------- In-app AI assistant (DeepSeek) ----------
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    # NOTE: "deepseek-chat" was deprecated on 2026-07-24 and is gone from the
    # API's model list, though it still responds today as a legacy alias — so it
    # is living on borrowed time, not broken. models.list() returns only
    # deepseek-v4-flash and deepseek-v4-pro, so we name flash directly.
    # Correction to the ticket: flash IS a thinking model (it returns
    # reasoning_content), not the "non-thinking" one the ticket described.
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"

    # USD per million tokens. DeepSeek have announced a price rise — re-check
    # these after any change, or the monthly kill-switch trips at the wrong time.
    AI_PRICE_INPUT_PER_M = float(os.getenv("AI_PRICE_INPUT_PER_M", "0.14"))
    AI_PRICE_OUTPUT_PER_M = float(os.getenv("AI_PRICE_OUTPUT_PER_M", "0.28"))

    AI_MAX_QUESTIONS_PER_USER_PER_DAY = int(os.getenv("AI_MAX_QUESTIONS_PER_USER_PER_DAY", "30"))
    # deepseek-v4-flash is a THINKING model: its reasoning tokens are billed and
    # counted as output. Measured on real questions, answers used 178–441 of the
    # ticket's 500 — too close to the ceiling, and hitting it truncates the Arabic
    # answer mid-sentence. At ~$0.65 per 1000 questions the headroom is cheap.
    AI_MAX_RESPONSE_TOKENS = int(os.getenv("AI_MAX_RESPONSE_TOKENS", "900"))
    AI_MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY_MESSAGES", "10"))

    # Kill-switch: once the month's spend passes this, the assistant stops
    # answering until someone raises it deliberately.
    AI_MONTHLY_BUDGET_USD = float(os.getenv("AI_MONTHLY_BUDGET_USD", "5.00"))


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


configs = {"development": DevConfig, "production": ProdConfig}
