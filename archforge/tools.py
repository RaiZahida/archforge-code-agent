"""Sandboxed tools the agent can call.

Design borrowed from Claude Code's tool layer: every tool has a name, a
description, a JSON schema for its input and a read-only flag. Errors never
crash the loop; they are returned to the model as tool results so it can fix
them. Every path is confined to the output directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import COMMAND_TIMEOUT_S, TOOL_OUTPUT_LIMIT


class ToolError(Exception):
    pass


def truncate(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[-limit // 2:]
    return f"{head}\n... [{len(text) - limit} chars truncated] ...\n{tail}"


def pytest_command(project_dir: Path) -> list[str]:
    """Command that runs pytest on `project_dir`.

    In the frozen Windows .exe there is no python.exe on the target machine, so
    the app re-launches itself with a hidden flag and runs pytest in-process.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-pytest", str(project_dir)]
    return [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", str(project_dir)]


def run_child(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run a helper process and return (exit code, combined output).

    A windowed (no-console) .exe has no usable stdout, so in frozen mode the child
    writes its output to a temp file named in ARCHFORGE_CHILD_OUT instead of a pipe.
    Raises subprocess.TimeoutExpired on timeout.
    """
    import tempfile
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flashing up
    if getattr(sys, "frozen", False):
        fd, out_file = tempfile.mkstemp(prefix="archforge_", suffix=".log")
        os.close(fd)
        env["ARCHFORGE_CHILD_OUT"] = out_file
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **kw)
            text = Path(out_file).read_text(encoding="utf-8", errors="replace")
        finally:
            try:
                os.remove(out_file)
            except OSError:
                pass
        return proc.returncode, text
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", **kw)
    return proc.returncode, proc.stdout + proc.stderr


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., str]
    read_only: bool = False

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class Workspace:
    """The output directory the agent is allowed to touch, plus run state."""
    root: Path
    plan: list[dict] = field(default_factory=list)
    finished: bool = False
    summary: str = ""
    files_written: set[str] = field(default_factory=set)
    last_test: dict | None = None

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        if not rel or not isinstance(rel, str):
            raise ToolError("path must be a non-empty string")
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"path '{rel}' escapes the output directory; use a relative path inside it")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ---------------------------------------------------------------- tools
    def write_file(self, path: str, content: str) -> str:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        self.files_written.add(self.rel(p))
        self._tick_plan(self.rel(p))
        return f"wrote {self.rel(p)} ({len(content.splitlines())} lines)"

    def read_file(self, path: str) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"file not found: {path}")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return truncate("\n".join(f"{i:4}| {l}" for i, l in enumerate(lines, 1)))

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"file not found: {path}")
        text = p.read_text(encoding="utf-8")
        n = text.count(old_text)
        if n == 0:
            raise ToolError("old_text not found; read_file first and copy the exact text")
        if n > 1:
            raise ToolError(f"old_text occurs {n} times; include more context so it is unique")
        p.write_text(text.replace(old_text, new_text), encoding="utf-8", newline="\n")
        return f"edited {self.rel(p)}"

    def list_dir(self, path: str = ".") -> str:
        p = self.resolve(path or ".")
        if not p.is_dir():
            raise ToolError(f"not a directory: {path}")
        items = []
        for q in sorted(p.rglob("*")):
            if any(part in ("__pycache__", ".pytest_cache") for part in q.parts):
                continue
            items.append(self.rel(q) + ("/" if q.is_dir() else f"  ({q.stat().st_size} B)"))
        return truncate("\n".join(items) or "(empty)")

    def run_tests(self) -> str:
        try:
            code, out = run_child(pytest_command(self.root), self.root, COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.last_test = {"passed": False, "exit_code": None, "output": "timeout"}
            raise ToolError(f"tests timed out after {COMMAND_TIMEOUT_S}s (an infinite loop or a GUI mainloop in tests?)")
        out = out.strip()
        # pytest exit codes: 0 all passed, 1 failures, 5 no tests collected
        self.last_test = {"passed": code == 0, "exit_code": code, "output": out[-2000:]}
        status = "ALL TESTS PASSED" if code == 0 else f"TESTS FAILED (exit {code})"
        return truncate(f"{status}\n{out}")

    def _tick_plan(self, rel: str) -> None:
        """Models often forget to update plan status, so mark items mentioning a written file."""
        name = rel.rsplit("/", 1)[-1]
        for it in self.plan:
            if it["status"] != "done" and (rel in it["task"] or name in it["task"]):
                it["status"] = "in_progress"

    def update_plan(self, items: list) -> str:
        clean = []
        for it in items:
            if isinstance(it, str):
                it = {"task": it, "status": "pending"}
            clean.append({"task": str(it.get("task", "")),
                          "status": it.get("status", "pending") if it.get("status") in
                          ("pending", "in_progress", "done") else "pending"})
        self.plan = clean
        done = sum(i["status"] == "done" for i in clean)
        return f"plan updated: {done}/{len(clean)} done"

    def finish(self, summary: str) -> str:
        self.finished = True
        self.summary = summary
        return "finish requested"

    # ---------------------------------------------------------------- registry
    def tools(self) -> dict[str, Tool]:
        s = lambda **props: {"type": "object", "properties": props,  # noqa: E731
                             "required": list(props), "additionalProperties": False}
        string = {"type": "string"}
        return {t.name: t for t in [
            Tool("update_plan",
                 "Create or update your task plan. Call it first with every file you will create, "
                 "then mark items done as you go.",
                 s(items={"type": "array", "items": {"type": "object", "properties": {
                     "task": string, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
                     "required": ["task", "status"]}}),
                 self.update_plan, read_only=True),
            Tool("write_file", "Create or overwrite a file (path relative to the project root). "
                 "Always write the COMPLETE file content.",
                 s(path=string, content=string), self.write_file),
            Tool("edit_file", "Replace one exact, unique snippet in an existing file. Prefer this for small fixes.",
                 s(path=string, old_text=string, new_text=string), self.edit_file),
            Tool("read_file", "Read a file with line numbers.", s(path=string), self.read_file, read_only=True),
            Tool("list_dir", "List all files under a directory of the project ('.' for root).",
                 s(path=string), self.list_dir, read_only=True),
            Tool("run_tests", "Run the project's pytest suite and return the result.",
                 {"type": "object", "properties": {}, "additionalProperties": False},
                 self.run_tests, read_only=True),
            Tool("finish", "Call when every planned file exists and all tests pass. "
                 "Give a short summary of what you built.",
                 s(summary=string), self.finish),
        ]}
