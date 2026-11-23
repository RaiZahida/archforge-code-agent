"""Minimal DeepSeek chat-completions client (OpenAI-compatible), stdlib only.

Using urllib instead of the openai SDK keeps the Windows .exe small and avoids
extra dependencies.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import API_BASE


class LLMError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, api_key: str, model: str, base_url: str = API_BASE,
                 timeout: int = 300, max_retries: int = 6, thinking: bool | None = None,
                 strip_additional_properties: bool = False):
        if not api_key:
            raise LLMError("API key is missing")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.thinking = thinking  # None = provider default
        self.strip_additional_properties = strip_additional_properties
        self.on_retry = None  # optional callback(reason: str, wait_s: float)

    @classmethod
    def for_provider(cls, provider: str, api_key: str, model: str | None = None) -> "DeepSeekClient":
        from .config import PROVIDERS
        cfg = PROVIDERS[provider]
        return cls(api_key, model or cfg["default_model"], base_url=cfg["base_url"],
                   strip_additional_properties=cfg["strip_additional_properties"])

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.0, max_tokens: int = 8192) -> dict:
        """Return {"message": {...}, "usage": {...}, "finish_reason": str}."""
        body: dict = {"model": self.model, "messages": messages,
                      "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            if self.strip_additional_properties:  # Gemini's schema subset rejects this key
                tools = json.loads(json.dumps(tools).replace(', "additionalProperties": false', ""))
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.thinking is not None:
            body["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        data = self._post("/chat/completions", body)
        choice = data["choices"][0]
        return {"message": choice["message"], "usage": data.get("usage", {}),
                "finish_reason": choice.get("finish_reason")}

    def _post(self, path: str, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                self.base_url + path, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                # 429 rate limit / 5xx server busy are worth retrying; 4xx are not
                if e.code in (429, 500, 502, 503) and attempt < self.max_retries:
                    if self.on_retry:
                        self.on_retry(f"HTTP {e.code} ({'rate limit' if e.code == 429 else 'server busy'})", delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"API HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    if self.on_retry:
                        self.on_retry(f"network: {e}", delay)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"Network error talking to the API: {e}") from None
        raise LLMError("unreachable")
