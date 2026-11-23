"""Agent-loop tests with a scripted fake LLM (no network, no API cost).

Same idea as claw-code's deterministic mock service: the fake client replays a
fixed sequence of tool calls so the loop, tools, repair cycle and budget guard
can be tested offline.
"""
import json
from datetime import datetime, timezone

import pytest

from archforge.agent import Agent
from archforge.cost import CostTracker, is_peak
from archforge.tools import ToolError, Workspace

USAGE = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800,
         "prompt_cache_miss_tokens": 200, "completion_tokens": 300}


def call(name, i, **args):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLLM:
    model = "deepseek-flash"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def chat(self, messages, tools=None, **kw):
        self.seen.append([m.copy() for m in messages])
        calls = self.turns.pop(0) if self.turns else []
        return {"message": {"role": "assistant", "content": "", "tool_calls": calls or None},
                "usage": USAGE, "finish_reason": "tool_calls"}


GOOD_MODULE = "def add(a, b):\n    return a + b\n"
BAD_MODULE = "def add(a, b):\n    return a - b\n"
TEST_FILE = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def test_happy_path(tmp_path):
    llm = FakeLLM([
        [call("update_plan", 1, items=[{"task": "calc.py", "status": "pending"},
                                       {"task": "tests", "status": "pending"}])],
        [call("write_file", 2, path="calc.py", content=GOOD_MODULE),
         call("write_file", 3, path="tests/test_calc.py", content=TEST_FILE)],
        [call("run_tests", 4)],
        [call("finish", 5, summary="calculator")],
    ])
    events = []
    res = Agent(llm, tmp_path, on_event=lambda k, d: events.append(k), quality_checks=False).run({"system": {}})
    assert res.status == "success" and res.tests_passed
    assert res.files == ["calc.py", "tests/test_calc.py"]
    assert (tmp_path / ".archforge" / "report.json").exists()
    assert {"start", "model", "tool", "done"} <= set(events)
    # system prompt is first and identical on every call (prefix-cache friendly)
    assert all(m[0]["role"] == "system" for m in llm.seen)
    assert len({m[0]["content"] for m in llm.seen}) == 1


def test_finish_with_failing_tests_triggers_repair(tmp_path):
    llm = FakeLLM([
        [call("write_file", 1, path="calc.py", content=BAD_MODULE),
         call("write_file", 2, path="tests/test_calc.py", content=TEST_FILE)],
        [call("finish", 3, summary="too early")],                 # tests are red -> pushed back
        [call("edit_file", 4, path="calc.py", old_text="a - b", new_text="a + b")],
        [call("finish", 5, summary="fixed")],
    ])
    res = Agent(llm, tmp_path, quality_checks=False).run({})
    assert res.status == "success" and res.tests_passed
    # the model received the failing pytest output after its first finish
    tool_msgs = [m for m in llm.seen[2] if m["role"] == "tool"]
    assert "TESTS FAILED" in tool_msgs[-1]["content"] or "not green" in tool_msgs[-1]["content"]


def test_tool_errors_are_returned_not_raised(tmp_path):
    bad_json = {"id": "x", "type": "function", "function": {"name": "write_file", "arguments": "{oops"}}
    llm = FakeLLM([
        [bad_json, call("read_file", 2, path="missing.py"), call("nope", 3),
         call("write_file", 4, path="../escape.txt", content="x")],
        [call("finish", 5, summary="done")],
    ])
    res = Agent(llm, tmp_path / "out").run({})
    errors = [m["content"] for m in llm.seen[1] if m["role"] == "tool"]
    assert all(e.startswith("ERROR") for e in errors) and len(errors) == 4
    assert not (tmp_path / "escape.txt").exists()
    assert res.status != "success"  # no tests were written, so it must not report success


def test_budget_guard_stops_run(tmp_path):
    llm = FakeLLM([[call("list_dir", i, path=".")] for i in range(50)])
    res = Agent(llm, tmp_path, budget_usd=0.0001).run({})
    assert res.status == "budget_exceeded"
    assert res.steps < 50


def test_max_steps(tmp_path):
    llm = FakeLLM([[call("list_dir", i, path=".")] for i in range(10)])
    res = Agent(llm, tmp_path, max_steps=3).run({})
    assert res.status == "max_steps" and res.steps == 3


def test_workspace_sandbox(tmp_path):
    ws = Workspace(tmp_path)
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ToolError):
            ws.resolve(bad)
    ws.write_file("pkg/a.py", "x = 1\n")
    with pytest.raises(ToolError):
        ws.edit_file("pkg/a.py", "y", "z")


def test_cost_peak_and_offpeak():
    peak = datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc)      # Monday 07:00 UTC
    off = datetime(2026, 11, 22, 7, 0, tzinfo=timezone.utc)       # Sunday
    assert is_peak(peak) and not is_peak(off)
    t = CostTracker("deepseek-v4-pro")
    c_peak = t.add(USAGE, now=peak)
    c_off = t.add(USAGE, now=off)
    expected_peak = (800 * 0.044 + 200 * 1.32 + 300 * 3.96) / 1e6
    assert c_peak == pytest.approx(expected_peak)
    assert c_off == pytest.approx(expected_peak / 2)
    assert t.summary()["cache_hit_rate"] == 0.8


def test_quality_checks_push_back_missing_deliverables(tmp_path):
    spec = {"domain_model": {"use_cases": [{"id": "ViewHelp", "label": "View Help"}], "states": ["Paused"]}}
    t3 = "def test_x():\n    assert True\n"
    llm = FakeLLM([
        [call("write_file", 1, path="tests/test_a.py", content=t3)],
        [call("finish", 2, summary="early")],                          # missing files -> pushed back
        [call("write_file", 3, path="main.py", content="def view_help():\n    pass\nPAUSED = 1\n"),
         call("write_file", 4, path="README.md", content="x"),
         call("write_file", 5, path="requirements.txt", content="pytest\n"),
         call("write_file", 6, path="tests/test_b.py", content=t3),
         call("write_file", 7, path="tests/test_c.py", content=t3)],
        [call("finish", 8, summary="done")],
    ])
    res = Agent(llm, tmp_path).run(spec)
    feedback = [m["content"] for m in llm.seen[2] if m["role"] == "tool"][-1]
    assert "missing main.py" in feedback and "View Help" in feedback and "Paused" in feedback
    assert res.status == "success" and res.quality_issues == []


class EmptyThenWorkLLM(FakeLLM):
    def chat(self, messages, tools=None, **kw):
        self.seen.append([m.copy() for m in messages])
        if len(self.seen) <= 2:  # two empty replies, as if the output limit was hit
            return {"message": {"role": "assistant", "content": ""}, "usage": USAGE, "finish_reason": "length"}
        return super().chat(messages, tools, **kw)


def test_empty_replies_get_specific_nudge(tmp_path):
    llm = EmptyThenWorkLLM([[call("finish", 1, summary="x")]])
    Agent(llm, tmp_path, quality_checks=False).run({})
    last_user = [m for m in llm.seen[2] if m["role"] == "user"][-1]["content"]
    assert "output limit" in last_user
    assert not any(m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls") for m in llm.seen[2])


def test_quota_exceeded_switches_model(tmp_path):
    from archforge.llm import QuotaExceeded

    class QuotaLLM(FakeLLM):
        model = "gemini-a"
        fallback_models = ["gemini-b"]

        def chat(self, messages, tools=None, **kw):
            if self.model == "gemini-a":
                raise QuotaExceeded("gemini-a: daily quota exceeded")
            return super().chat(messages, tools, **kw)

    events = []
    llm = QuotaLLM([[call("finish", 1, summary="x")]])
    res = Agent(llm, tmp_path, quality_checks=False, on_event=lambda k, d: events.append((k, d))).run({})
    assert llm.model == "gemini-b"
    assert ("switch", {"from": "gemini-a", "to": "gemini-b", "reason": "gemini-a: daily quota exceeded"}) in events
    assert res.cost["model"] == "gemini-b"


def test_resume_lists_existing_files(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("x = 1\n")
    llm = FakeLLM([[call("finish", 1, summary="x")]])
    Agent(llm, tmp_path, quality_checks=False).run({})
    first_user = llm.seen[0][1]["content"]
    assert "RESUMING" in first_user and "app/models.py" in first_user
