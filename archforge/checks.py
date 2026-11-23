"""Static checks on a generated project (no LLM, no display needed).

* reachable_modules: follow imports from main.py so dead/orphaned files don't count
  towards use-case coverage.
* import_check: actually import every reachable module in a subprocess to catch
  broken imports (e.g. a UI module importing a class that a later edit removed).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path


def _module_file(root: Path, name: str) -> Path | None:
    parts = name.split(".")
    for cand in (root.joinpath(*parts).with_suffix(".py"), root.joinpath(*parts, "__init__.py")):
        if cand.is_file():
            return cand
    return None


def _imports(path: Path, root: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()
    pkg = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)] if pkg else []
                base = ".".join([p for p in up if p] + ([base] if base else []))
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)  # "from pkg import module"
    return {n for n in names if n}


def reachable_modules(root: Path, entry: str = "main.py") -> list[Path]:
    root = Path(root)
    start = root / entry
    if not start.is_file():
        return []
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for name in _imports(cur, root):
            parts = name.split(".")
            for i in range(1, len(parts) + 1):  # parent packages are imported too
                f = _module_file(root, ".".join(parts[:i]))
                if f and f not in seen:
                    seen.add(f)
                    stack.append(f)
    return sorted(seen)


_IMPORT_SCRIPT = r"""
import importlib, json, sys, traceback
root, mods = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, root)
bad = {}
for m in mods:
    try:
        importlib.import_module(m)
    except ModuleNotFoundError as e:
        if e.name not in ("tkinter", "_tkinter"):  # tkinter missing = environment, not code
            bad[m] = f"{type(e).__name__}: {e}"
    except Exception as e:
        bad[m] = f"{type(e).__name__}: {e}"
print(json.dumps(bad))
"""


def run_import_script(root: str, mods_json: str) -> int:
    """Entry used by the frozen .exe (`--import-check`), which has no python.exe."""
    sys.argv = ["x", root, mods_json]
    exec(compile(_IMPORT_SCRIPT, "<import-check>", "exec"), {"__name__": "__main__"})
    return 0


def import_check(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    mods = []
    for f in reachable_modules(root):
        rel = f.relative_to(root).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        if parts and parts != ("main",):
            mods.append(".".join(parts))
    if not mods:
        return {}
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--import-check", str(root), json.dumps(mods)]
    else:
        cmd = [sys.executable, "-c", _IMPORT_SCRIPT, str(root), json.dumps(mods)]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60, env=env)
        return json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"?": p.stderr[-300:]}
    except Exception as e:  # never let the checker crash the agent
        return {"?": f"import check could not run: {e}"}
