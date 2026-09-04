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
    # deepseek-v4-flash reasons by default and bills that reasoning as output.
    # Left on, a broad question spends the ENTIRE budget thinking and returns an
    # empty answer (measured: 2500 tokens, 6911 chars of reasoning, 0 of answer).
    # "disabled" is the non-thinking mode the ticket asked for. Set to "enabled"
    # only if you want reasoning and are ready to pay for it.
    AI_THINKING_MODE = os.getenv("AI_THINKING_MODE", "disabled")
    AI_MAX_RESPONSE_TOKENS = int(os.getenv("AI_MAX_RESPONSE_TOKENS", "900"))
    AI_MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY_MESSAGES", "10"))

    # Kill-switch: once the month's spend passes this, the assistant stops
    # answering until someone raises it deliberately.
    AI_MONTHLY_BUDGET_USD = float(os.getenv("AI_MONTHLY_BUDGET_USD", "5.00"))

    # ---------- Upload size cap (SEC-3) ----------
    # Global request-body ceiling — any POST larger than this gets a
    # 413 from Werkzeug before ever reaching a view. Only one upload
    # site exists today (the company logo, 2 MB max); every other
    # POST is a small form. 3 MB leaves 1 MB of headroom.
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024

    # ---------- SMTP (SEC-1) ----------
    # Leave SMTP_HOST blank to log emails to the Flask console only —
    # dev-friendly default. For Resend: smtp.resend.com : 587, user
    # "resend", password = Resend API key.
    SMTP_HOST     = os.getenv("SMTP_HOST", "")
    SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER     = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM     = os.getenv("SMTP_FROM", "no-reply@elyasmin.manasety.ai")
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "مزرعة الياسمين")
    SMTP_USE_TLS  = os.getenv("SMTP_USE_TLS", "true").lower() == "true"


class DevConfig(Config):
    DEBUG = True


class ProdConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


configs = {"development": DevConfig, "production": ProdConfig}
