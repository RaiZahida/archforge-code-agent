"""Model catalogue, prices and defaults.

Prices are USD per 1M tokens from the official DeepSeek pricing page
(https://api-docs.deepseek.com/quick_start/pricing). DeepSeek bills off-peak
hours at half price; peak is 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.
"""
from __future__ import annotations

from dataclasses import dataclass

API_BASE = "https://api.deepseek.com"

# OpenAI-compatible providers. DeepSeek is the one the evaluation requires;
# Gemini (free tier from Google AI Studio) is a development fallback.
PROVIDERS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY",
                 "default_model": "deepseek-flash", "strip_additional_properties": False},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_env": "GEMINI_API_KEY", "default_model": "gemini-3.5-flash",
               "strip_additional_properties": True,
               # free-tier quotas are per model, so when one is used up we move to the next
               "fallback_models": ["gemini-3.7-flash", "gemini-3.8-flash", "gemini-flash-latest",
                                   "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
                                   "gemini-2.5-flash"]},
}
DEFAULT_PROVIDER = "deepseek"

# Use the cheap model while developing; the evaluation requires deepseek-v4-pro.
DEV_MODEL = "deepseek-flash"
FINAL_MODEL = "deepseek-v4-pro"
DEFAULT_MODEL = DEV_MODEL

DEFAULT_BUDGET_USD = 1.00      # hard stop: the task requires < $1 per run
DEFAULT_MAX_STEPS = 60         # max model turns in one run
DEFAULT_MAX_FIX_ROUNDS = 4     # how many times the agent may retry failing tests
TOOL_OUTPUT_LIMIT = 6000       # chars of tool output sent back to the model
COMMAND_TIMEOUT_S = 180


@dataclass(frozen=True)
class Price:
    cache_hit: float   # input tokens served from DeepSeek's prefix cache
    cache_miss: float  # input tokens not cached
    output: float


# peak prices; off-peak = peak / 2
PRICES: dict[str, Price] = {
    "deepseek-flash": Price(cache_hit=0.006, cache_miss=0.30, output=1.20),
    "deepseek-v4-flash": Price(cache_hit=0.006, cache_miss=0.30, output=1.20),
    "deepseek-v4-pro": Price(cache_hit=0.044, cache_miss=1.32, output=3.96),
}

MODELS = list(PRICES)
FREE_PRICE = Price(0.0, 0.0, 0.0)  # Gemini free tier


def price_for(model: str) -> Price:
    if model.startswith(("gemini", "gemma")):
        return FREE_PRICE
    return PRICES.get(model, PRICES["deepseek-v4-pro"])  # unknown model: assume the dearest
