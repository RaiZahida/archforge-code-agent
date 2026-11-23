"""Command-line entry point.

    python -m archforge run --doc DOC.md --view VIEW.md --out OUTPUT_DIR [--model M] [--budget 1.0]

The API key is read from --api-key or the DEEPSEEK_API_KEY environment variable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config


def _run_pytest_inprocess(argv: list[str]) -> int:
    """Hidden mode used by the frozen .exe to run pytest without a Python install."""
    import pytest
    return pytest.main(["-q", "--no-header", "-p", "no:cacheprovider", *argv])


def _print_event(kind: str, d: dict) -> None:
    if kind == "start":
        print(f"ArchForge | model={d['model']} | budget=${d['budget']:.2f} | out={d['out_dir']}")
    elif kind == "waiting":
        print(f"[step {d['step']:>2}] waiting for the model...", flush=True)
    elif kind == "retry":
        print(f"   ... {d['reason']} -> retrying in {d['wait_s']:.0f}s", flush=True)
    elif kind == "model":
        print(f"[step {d['step']:>2}] {d['tool_calls']} tool call(s) | spent ${d['cost_usd']:.4f}", flush=True)
    elif kind == "tool":
        mark = "ok " if d["ok"] else "ERR"
        print(f"   {mark} {d['name']}({d['args']})" + ("" if d["ok"] else f" -> {d['output'][:160]}"), flush=True)
    elif kind == "repair":
        why = "; ".join(d.get("problems") or []) or "tests failing"
        print(f"   not done yet -> repair round ({d['rounds_left']} left): {why[:200]}", flush=True)
    elif kind == "done":
        c = d["cost"]
        print(f"\nStatus: {d['status']} | tests passed: {d['tests_passed']} | files: {len(d['files'])} | "
              f"steps: {d['steps']} | {d['duration_s']}s")
        print(f"Cost: ${c['cost_usd']:.4f} ({c['input_tokens']} in, cache hit {c['cache_hit_rate']:.0%}, "
              f"{c['output_tokens']} out)")
        if d["error"]:
            print("Error:", d["error"])
        for q in d.get("quality_issues") or []:
            print("Quality issue:", q)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--run-pytest"]:
        return _run_pytest_inprocess(argv[1:])
    if not argv:  # double-clicked: open the GUI
        from .gui import main as gui_main
        return gui_main()

    ap = argparse.ArgumentParser(prog="archforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate a project from architecture documents")
    r.add_argument("--doc", type=Path, required=True)
    r.add_argument("--view", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--provider", default=config.DEFAULT_PROVIDER, choices=list(config.PROVIDERS))
    r.add_argument("--model", default=None, help="model id (default depends on provider)")
    r.add_argument("--budget", type=float, default=config.DEFAULT_BUDGET_USD)
    r.add_argument("--max-steps", type=int, default=config.DEFAULT_MAX_STEPS)
    r.add_argument("--api-key", default="", help="or set DEEPSEEK_API_KEY / GEMINI_API_KEY")
    sub.add_parser("gui", help="open the desktop app")
    a = ap.parse_args(argv)

    if a.cmd == "gui":
        from .gui import main as gui_main
        return gui_main()

    from .agent import Agent
    from .llm import DeepSeekClient
    from .preprocess import build_spec, compact_spec

    spec = build_spec(a.doc.read_text(encoding="utf-8"), a.view.read_text(encoding="utf-8"))
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / ".archforge").mkdir(exist_ok=True)
    (a.out / ".archforge" / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    key = a.api_key or os.environ.get(config.PROVIDERS[a.provider]["key_env"], "")
    client = DeepSeekClient.for_provider(a.provider, key, a.model)
    agent = Agent(client, a.out, budget_usd=a.budget, max_steps=a.max_steps, on_event=_print_event)
    result = agent.run(compact_spec(spec))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
