"""ASSISTANT: thin wrapper around DeepSeek's OpenAI-compatible endpoint.

Kept in its own module so the tests can monkeypatch `get_deepseek_client` and
exercise every guard in the route without a network call or an API key.
"""
from flask import current_app
from openai import OpenAI


def get_deepseek_client() -> OpenAI:
    return OpenAI(
        api_key=current_app.config["DEEPSEEK_API_KEY"],
        base_url=current_app.config["DEEPSEEK_BASE_URL"],
        timeout=15.0,
        # At most one retry — a network wobble must not silently double the bill.
        max_retries=1,
    )
