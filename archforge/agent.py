"""The agent loop: model -> tool calls -> tool results -> model ... -> finish.

Mirrors the core query loop of Claude Code in a simplified, single-agent form:
  * the static system prompt goes first (prefix-cache friendly),
  * the model decides which tools to call; results go back as `tool` messages,
  * tool errors are returned to the model instead of crashing the run,
  * `finish` triggers an automatic test run; failures are fed back for repair,
  * a cost tracker enforces a hard budget.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from . import config
from .cost import BudgetExceeded, CostTracker
from .prompts import SYSTEM_PROMPT, fix_message, quality_message, task_message
from .tools import ToolError, Workspace

EventFn = Callable[[str, dict], None]


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **kw) -> dict: ...


@dataclass
class RunResult:
    status: str                 # "success" | "tests_failing" | "budget_exceeded" | "max_steps" | "cancelled" | "error"
    steps: int
    files: list[str]
    tests_passed: bool
    summary: str
    cost: dict
    duration_s: float
    error: str = ""
    plan: list[dict] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Agent:
    def __init__(self, client: ChatClient, out_dir: str | Path, *,
                 budget_usd: float = config.DEFAULT_BUDGET_USD,
                 max_steps: int = config.DEFAULT_MAX_STEPS,
                 max_fix_rounds: int = config.DEFAULT_MAX_FIX_ROUNDS,
                 on_event: EventFn | None = None, quality_checks: bool = True):
        self.client = client
        self.ws = Workspace(Path(out_dir))
        self.cost = CostTracker(model=client.model, budget_usd=budget_usd)
        self.max_steps = max_steps
        self.fix_rounds_left = max_fix_rounds
        self.on_event = on_event or (lambda kind, data: None)
        self.quality_checks = quality_checks
        self.cancel_event = threading.Event()
        self.tools = self.ws.tools()
        self.messages: list[dict] = []
        self.log_dir = self.ws.root / ".archforge"
        if hasattr(client, "on_retry"):
            client.on_retry = lambda reason, wait: self._emit("retry", {"reason": reason, "wait_s": wait})

    # ------------------------------------------------------------------ public
    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self, spec: dict) -> RunResult:
        t0 = time.time()
        self.spec = spec
        self.log_dir.mkdir(exist_ok=True)
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": task_message(spec)}]
        schemas = [t.schema() for t in self.tools.values()]
        status, error, steps, nudges = "max_steps", "", 0, 0
        self._emit("start", {"model": self.client.model, "out_dir": str(self.ws.root),
                             "budget": self.cost.budget_usd})
        try:
            for steps in range(1, self.max_steps + 1):
                if self.cancel_event.is_set():
                    status = "cancelled"
                    break
                self.cost.check()
                self._emit("waiting", {"step": steps})
                resp = self.client.chat(self.messages, tools=schemas)
                step_cost = self.cost.add(resp.get("usage", {}))
                msg = resp["message"]
                self.messages.append(self._assistant_entry(msg))
                self._emit("model", {"step": steps, "text": msg.get("content") or "",
                                     "tool_calls": len(msg.get("tool_calls") or []),
                                     "step_cost": step_cost, **self.cost.summary()})
                calls = msg.get("tool_calls") or []
                if not calls:
                    nudges += 1
                    if nudges > 3:
                        status, error = "error", "model stopped calling tools"
                        break
                    self.messages.append({"role": "user", "content":
                                          "Continue working using the tools. Call finish when everything "
                                          "is written and the tests pass."})
                    continue
                nudges = 0
                for call in calls:
                    self.messages.append(self._execute(call))
                if self.ws.finished:
                    status = "success"
                    break
        except BudgetExceeded as e:
            status, error = "budget_exceeded", str(e)
        except Exception as e:  # network/API errors: stop cleanly and keep what was written
            status, error = "error", f"{type(e).__name__}: {e}"

        if status in ("budget_exceeded", "max_steps", "cancelled", "error") and self.ws.files_written:
            try:  # final verification so the report is honest about the output
                self.ws.run_tests()
            except ToolError:
                pass
        tests_passed = bool(self.ws.last_test and self.ws.last_test["passed"])
        if status == "success" and not tests_passed:
            status = "tests_failing"
        issues = self.deliverable_problems() if self.ws.files_written else []
        result = RunResult(status=status, steps=steps, files=sorted(self.ws.files_written),
                           tests_passed=tests_passed, summary=self.ws.summary,
                           cost=self.cost.summary(), duration_s=round(time.time() - t0, 1),
                           error=error, plan=self.ws.plan, quality_issues=issues)
        self._write_logs(result)
        self._emit("done", result.to_dict())
        return result

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _assistant_entry(msg: dict) -> dict:
        entry = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            entry["tool_calls"] = msg["tool_calls"]
        # DeepSeek thinking mode: reasoning must be passed back during tool-call turns
        if msg.get("reasoning_content"):
            entry["reasoning_content"] = msg["reasoning_content"]
        return entry

    def _execute(self, call: dict) -> dict:
        name = call.get("function", {}).get("name", "")
        raw = call.get("function", {}).get("arguments") or "{}"
        tool = self.tools.get(name)
        ok = True
        try:
            if tool is None:
                raise ToolError(f"unknown tool '{name}'. Available: {', '.join(self.tools)}")
            try:
                args = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ToolError(f"arguments are not valid JSON ({e}); resend the call") from None
            if not isinstance(args, dict):
                raise ToolError("arguments must be a JSON object")
            output = tool.func(**args)
            if name == "finish":
                output = self._verify_finish()
        except ToolError as e:
            ok, output = False, f"ERROR: {e}"
        except TypeError as e:  # wrong/missing arguments
            ok, output = False, f"ERROR: bad arguments for {name}: {e}"
        except Exception as e:  # never let one tool crash the run
            ok, output = False, f"ERROR: {type(e).__name__}: {e}"
        self._emit("tool", {"name": name, "ok": ok, "args": self._short_args(name, raw),
                            "output": output[:400], "plan": self.ws.plan})
        return {"role": "tool", "tool_call_id": call.get("id", ""), "content": output}

    def deliverable_problems(self) -> list[str]:
        """Static checks run on finish: required files exist and every use case shows up in code."""
        root, problems = self.ws.root, []
        if not self.quality_checks:
            return problems
        for f in ("main.py", "README.md", "requirements.txt"):
            if not (root / f).is_file():
                problems.append(f"missing {f}")
        tests = [p for p in (root / "tests").glob("test_*.py")] if (root / "tests").is_dir() else []
        if len(tests) < 3:
            problems.append(f"only {len(tests)} test file(s) in tests/; write at least 3")
        source = ""
        for p in root.rglob("*.py"):
            if ".archforge" not in p.parts and "tests" not in p.parts:
                source += p.read_text(encoding="utf-8", errors="ignore").lower()
        flat = source.replace("_", "").replace(" ", "")
        spec = getattr(self, "spec", {}) or {}
        for uc in spec.get("domain_model", {}).get("use_cases", []):
            label = uc.get("label") or uc.get("id", "")
            if label and label.lower() not in source and label.lower().replace(" ", "") not in flat:
                problems.append(f"use case '{label}' has no visible UI action (no button/label/function named after it)")
        for st in spec.get("domain_model", {}).get("states", []):
            if st.lower() not in flat:
                problems.append(f"state '{st}' from the state diagram is not implemented")
        return problems

    def _verify_finish(self) -> str:
        """finish => run tests and quality checks; if anything fails and fix rounds remain, push back."""
        try:
            report = self.ws.run_tests()
        except ToolError as e:
            report = f"TESTS FAILED: {e}"
        tests_ok = bool(self.ws.last_test and self.ws.last_test["passed"])
        problems = self.deliverable_problems()
        if tests_ok and not problems:
            return "Verified: all tests pass and all deliverables are present. Run complete."
        if self.fix_rounds_left > 0:
            self.fix_rounds_left -= 1
            self.ws.finished = False
            self._emit("repair", {"rounds_left": self.fix_rounds_left, "problems": problems})
            if not tests_ok:
                return fix_message(report, self.fix_rounds_left) + (
                    "\nAlso fix:\n- " + "\n- ".join(problems) if problems else "")
            return quality_message(problems, self.fix_rounds_left)
        self.quality_issues = problems
        return "Checks still failing and no fix rounds left. Run ends."

    @staticmethod
    def _short_args(name: str, raw: str) -> str:
        try:
            args = json.loads(raw)
        except Exception:
            return raw[:120]
        if name in ("write_file", "edit_file", "read_file"):
            return args.get("path", "")
        if name == "update_plan":
            return f"{len(args.get('items', []))} items"
        return json.dumps(args)[:120]

    def _emit(self, kind: str, data: dict) -> None:
        try:
            self.on_event(kind, data)
        except Exception:
            pass

    def _write_logs(self, result: RunResult) -> None:
        (self.log_dir / "report.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        with (self.log_dir / "transcript.jsonl").open("w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        (self.log_dir / "cost_per_call.json").write_text(json.dumps(self.cost.history, indent=1), encoding="utf-8")
