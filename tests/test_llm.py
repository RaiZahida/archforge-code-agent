"""DeepSeekClient against a local mock HTTP server (no real API calls)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from archforge.llm import DeepSeekClient, LLMError


def serve(responses):
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"auth": self.headers["Authorization"], "body": body})
            code, payload = responses.pop(0)
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, received


OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 5, "completion_tokens": 1}}


def test_chat_sends_tools_and_parses_reply():
    srv, rec = serve([(200, OK)])
    c = DeepSeekClient("sk-test", "deepseek-flash", base_url=f"http://127.0.0.1:{srv.server_port}")
    out = c.chat([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "t"}}])
    srv.shutdown()
    assert out["message"]["content"] == "hi" and out["usage"]["prompt_tokens"] == 5
    assert rec[0]["auth"] == "Bearer sk-test"
    assert rec[0]["body"]["model"] == "deepseek-flash" and rec[0]["body"]["tool_choice"] == "auto"


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("archforge.llm.time.sleep", lambda s: None)
    srv, rec = serve([(429, {"error": "busy"}), (200, OK)])
    c = DeepSeekClient("k", "deepseek-flash", base_url=f"http://127.0.0.1:{srv.server_port}")
    assert c.chat([{"role": "user", "content": "x"}])["message"]["content"] == "hi"
    srv.shutdown()
    assert len(rec) == 2


def test_insufficient_balance_is_clear_error():
    srv, _ = serve([(402, {"error": {"message": "Insufficient Balance"}})])
    c = DeepSeekClient("k", "deepseek-flash", base_url=f"http://127.0.0.1:{srv.server_port}")
    with pytest.raises(LLMError, match="402.*Insufficient Balance"):
        c.chat([{"role": "user", "content": "x"}])
    srv.shutdown()


def test_missing_key():
    with pytest.raises(LLMError):
        DeepSeekClient("", "deepseek-flash")


def test_gemini_provider_strips_additional_properties():
    srv, rec = serve([(200, OK)])
    c = DeepSeekClient.for_provider("gemini", "g-key")
    assert c.model.startswith("gemini") and "generativelanguage" in c.base_url
    c.base_url = f"http://127.0.0.1:{srv.server_port}"
    tool = {"type": "function", "function": {"name": "t", "parameters": {
        "type": "object", "properties": {}, "additionalProperties": False}}}
    c.chat([{"role": "user", "content": "x"}], tools=[tool])
    srv.shutdown()
    assert "additionalProperties" not in json.dumps(rec[0]["body"])


def test_gemini_is_free_in_cost_tracker():
    from archforge.cost import CostTracker
    t = CostTracker("gemini-3.8-flash")
    assert t.add({"prompt_tokens": 10000, "completion_tokens": 5000}) == 0.0


def test_daily_quota_raises_without_retry(monkeypatch):
    from archforge.llm import QuotaExceeded
    monkeypatch.setattr("archforge.llm.time.sleep", lambda s: None)
    srv, rec = serve([(429, {"error": {"message": "Quota exceeded for metric: requests per day limit exceeded"}})])
    c = DeepSeekClient("k", "gemini-x", base_url=f"http://127.0.0.1:{srv.server_port}")
    with pytest.raises(QuotaExceeded):
        c.chat([{"role": "user", "content": "x"}])
    srv.shutdown()
    assert len(rec) == 1


def test_persistent_503_raises_model_busy_when_fallbacks_exist(monkeypatch):
    from archforge.llm import ModelBusy
    monkeypatch.setattr("archforge.llm.time.sleep", lambda s: None)
    srv, rec = serve([(503, {"error": "busy"})] * 10)
    c = DeepSeekClient("k", "gemini-x", base_url=f"http://127.0.0.1:{srv.server_port}")
    c.fallback_models = ["gemini-y"]
    with pytest.raises(ModelBusy):
        c.chat([{"role": "user", "content": "x"}])
    srv.shutdown()
    assert len(rec) == 4
