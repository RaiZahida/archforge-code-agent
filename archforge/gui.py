"""ArchForge desktop app (tkinter): configure the API, pick inputs and output, run the agent.

The agent runs on a worker thread; it reports progress through `on_event`, which
puts events on a queue that the Tk main loop drains every 100 ms (tkinter is not
thread-safe, so widgets are only touched from the main thread).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from . import config

SETTINGS_FILE = Path.home() / ".archforge_settings.json"

MODEL_CHOICES = {
    "deepseek": ["deepseek-v4-pro", "deepseek-flash"],
    "gemini": ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash",
               "gemini-flash-latest", "gemini-3.1-flash-lite"],
}


# --------------------------------------------------------------------------- settings
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if os.name != "nt":
            os.chmod(SETTINGS_FILE, 0o600)  # the file may hold an API key
    except OSError:
        pass


def bundled_examples() -> tuple[str, str]:
    """Sample inputs shipped with the app (inside the .exe bundle or the repo)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    d = base / "examples" / "inputs"
    doc, view = d / "Architecture_Documentation.md", d / "Architecture_View.md"
    return (str(doc) if doc.exists() else "", str(view) if view.exists() else "")


def open_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------- app
def main() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from .agent import Agent
    from .llm import DeepSeekClient
    from .preprocess import build_spec, compact_spec

    s = load_settings()
    ex_doc, ex_view = bundled_examples()

    root = tk.Tk()
    root.title("ArchForge – Code Agent")
    root.geometry("1100x760")
    root.minsize(900, 620)
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
    style.configure("Run.TButton", font=("Segoe UI", 11, "bold"), padding=8)

    v = {
        "provider": tk.StringVar(value=s.get("provider", "gemini")),
        "model": tk.StringVar(value=s.get("model", "")),
        "key": tk.StringVar(value=s.get("keys", {}).get(s.get("provider", "gemini"), "")),
        "remember": tk.BooleanVar(value=bool(s.get("keys"))),
        "doc": tk.StringVar(value=s.get("doc") or ex_doc),
        "view": tk.StringVar(value=s.get("view") or ex_view),
        "out": tk.StringVar(value=s.get("out") or str(Path.home() / "ArchForge_output" / "space_fractions")),
        "budget": tk.DoubleVar(value=s.get("budget", config.DEFAULT_BUDGET_USD)),
        "fresh": tk.BooleanVar(value=True),
        "status": tk.StringVar(value="Ready."),
        "cost": tk.StringVar(value="Cost: $0.0000 | tokens in 0 / out 0 | steps 0"),
    }
    state = {"agent": None, "thread": None}
    events: queue.Queue = queue.Queue()

    # ---------------- layout
    top = ttk.Frame(root, padding=12)
    top.pack(fill="x")
    ttk.Label(top, text="ArchForge", style="Title.TLabel").pack(side="left")
    ttk.Label(top, text="  architecture documents → complete, tested desktop project").pack(side="left")

    cfg = ttk.LabelFrame(root, text="1. Model API", padding=10)
    cfg.pack(fill="x", padx=12)
    ttk.Label(cfg, text="Provider").grid(row=0, column=0, sticky="w")
    prov = ttk.Combobox(cfg, textvariable=v["provider"], values=list(MODEL_CHOICES), state="readonly", width=12)
    prov.grid(row=0, column=1, sticky="w", padx=6)
    ttk.Label(cfg, text="Model").grid(row=0, column=2, sticky="w", padx=(16, 0))
    model_box = ttk.Combobox(cfg, textvariable=v["model"], width=24)
    model_box.grid(row=0, column=3, sticky="w", padx=6)
    ttk.Label(cfg, text="API key").grid(row=0, column=4, sticky="w", padx=(16, 0))
    key_entry = ttk.Entry(cfg, textvariable=v["key"], show="•", width=36)
    key_entry.grid(row=0, column=5, sticky="we", padx=6)
    show = tk.BooleanVar(value=False)
    ttk.Checkbutton(cfg, text="show", variable=show,
                    command=lambda: key_entry.config(show="" if show.get() else "•")).grid(row=0, column=6)
    ttk.Checkbutton(cfg, text="remember key on this PC", variable=v["remember"]).grid(row=1, column=5, sticky="w", padx=6)
    cfg.columnconfigure(5, weight=1)

    def on_provider(*_):
        p = v["provider"].get()
        model_box["values"] = MODEL_CHOICES[p]
        if v["model"].get() not in MODEL_CHOICES[p]:
            v["model"].set(MODEL_CHOICES[p][0])
        v["key"].set(load_settings().get("keys", {}).get(p, "") or os.environ.get(config.PROVIDERS[p]["key_env"], ""))
    prov.bind("<<ComboboxSelected>>", on_provider)
    on_provider()

    io = ttk.LabelFrame(root, text="2. Inputs and output", padding=10)
    io.pack(fill="x", padx=12, pady=8)

    def file_row(r, label, var, kind):
        ttk.Label(io, text=label).grid(row=r, column=0, sticky="w")
        ttk.Entry(io, textvariable=var).grid(row=r, column=1, sticky="we", padx=6, pady=2)

        def browse():
            if kind == "dir":
                p = filedialog.askdirectory(title=label)
            else:
                p = filedialog.askopenfilename(title=label, filetypes=[("Markdown", "*.md"), ("All files", "*.*")])
            if p:
                var.set(p)
        ttk.Button(io, text="Browse…", command=browse).grid(row=r, column=2)

    file_row(0, "Architecture documentation (.md)", v["doc"], "file")
    file_row(1, "Architecture views / UML (.md)", v["view"], "file")
    file_row(2, "Output folder", v["out"], "dir")
    opts = ttk.Frame(io)
    opts.grid(row=3, column=1, sticky="w", pady=(6, 0))
    ttk.Label(opts, text="Budget (USD)").pack(side="left")
    ttk.Spinbox(opts, from_=0.05, to=5.0, increment=0.05, textvariable=v["budget"], width=6).pack(side="left", padx=6)
    ttk.Checkbutton(opts, text="start fresh (clear output folder)", variable=v["fresh"]).pack(side="left", padx=12)
    io.columnconfigure(1, weight=1)

    actions = ttk.Frame(root, padding=(12, 0))
    actions.pack(fill="x")
    run_btn = ttk.Button(actions, text="▶  Generate project", style="Run.TButton")
    run_btn.pack(side="left")
    stop_btn = ttk.Button(actions, text="■ Stop", state="disabled")
    stop_btn.pack(side="left", padx=8)
    open_btn = ttk.Button(actions, text="Open output folder", command=lambda: open_path(v["out"].get()))
    open_btn.pack(side="left", padx=8)
    ttk.Label(actions, textvariable=v["cost"]).pack(side="right")

    ttk.Label(root, textvariable=v["status"], relief="sunken", anchor="w", padding=4).pack(fill="x", side="bottom")
    body = ttk.PanedWindow(root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=12, pady=8)
    plan_frame = ttk.LabelFrame(body, text="Plan", padding=6)
    plan = ttk.Treeview(plan_frame, columns=("status",), show="tree headings", height=12)
    plan.heading("#0", text="Task")
    plan.heading("status", text="Status")
    plan.column("status", width=90, anchor="center")
    plan.pack(fill="both", expand=True)
    body.add(plan_frame, weight=1)
    log_frame = ttk.LabelFrame(body, text="Agent log", padding=6)
    log = tk.Text(log_frame, wrap="word", font=("Consolas", 10), bg="#101418", fg="#d7dde4",
                  insertbackground="#d7dde4", state="disabled")
    scroll = ttk.Scrollbar(log_frame, command=log.yview)
    log.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    log.pack(fill="both", expand=True)
    for tag, color in (("ok", "#7ee787"), ("err", "#ff7b72"), ("info", "#79c0ff"), ("warn", "#e3b341")):
        log.tag_configure(tag, foreground=color)
    body.add(log_frame, weight=3)

    # ---------------- helpers
    def write(text: str, tag: str = "") -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n", tag)
        log.see("end")
        log.configure(state="disabled")

    def set_plan(items):
        plan.delete(*plan.get_children())
        for it in items or []:
            mark = {"done": "✔ done", "in_progress": "… doing"}.get(it.get("status"), "pending")
            plan.insert("", "end", text=it.get("task", ""), values=(mark,))

    def handle(kind: str, d: dict) -> None:
        if kind == "start":
            write(f"Model {d['model']} | budget ${d['budget']:.2f} | output {d['out_dir']}", "info")
        elif kind == "waiting":
            v["status"].set(f"Step {d['step']}: waiting for the model…")
        elif kind == "model":
            v["cost"].set(f"Cost: ${d['cost_usd']:.4f} | tokens in {d['input_tokens']} / out "
                          f"{d['output_tokens']} | steps {d['step']}")
            if d.get("text"):
                write(d["text"].strip()[:400])
        elif kind == "tool":
            write(f"{'✔' if d['ok'] else '✖'} {d['name']}({d['args']})", "ok" if d["ok"] else "err")
            if not d["ok"]:
                write("   " + d["output"][:300], "err")
            if d.get("plan"):
                set_plan(d["plan"])
        elif kind == "repair":
            write("↻ Verification failed, agent is repairing: " + "; ".join(d.get("problems") or ["tests failing"]), "warn")
        elif kind == "retry":
            write(f"… {d['reason']}, retrying in {d['wait_s']:.0f}s", "warn")
        elif kind == "switch":
            write(f"⇄ {d['reason']} → switching to {d['to']}", "warn")
        elif kind == "empty":
            write(f"(model replied without a tool call: {d['finish_reason']}) nudging…", "warn")
        elif kind == "done":
            c = d["cost"]
            ok = d["status"] == "success"
            write(f"\n{'SUCCESS' if ok else d['status'].upper()} | tests passed: {d['tests_passed']} | "
                  f"{len(d['files'])} files | {d['steps']} steps | {d['duration_s']}s | ${c['cost_usd']:.4f}",
                  "ok" if ok else "err")
            for q in d.get("quality_issues") or []:
                write("quality issue: " + q, "warn")
            if d.get("error"):
                write("error: " + d["error"], "err")
            v["status"].set("Done. Open the output folder and run main.py." if ok else f"Finished: {d['status']}")
            run_btn.config(state="normal")
            stop_btn.config(state="disabled")
        elif kind == "crash":
            write(d["text"], "err")
            v["status"].set("Error, see log.")
            run_btn.config(state="normal")
            stop_btn.config(state="disabled")

    def pump():
        try:
            while True:
                handle(*events.get_nowait())
        except queue.Empty:
            pass
        root.after(100, pump)

    def worker(params: dict) -> None:
        try:
            spec = build_spec(Path(params["doc"]).read_text(encoding="utf-8"),
                              Path(params["view"]).read_text(encoding="utf-8"))
            out = Path(params["out"])
            if params["fresh"] and out.exists():
                import shutil
                shutil.rmtree(out)
            (out / ".archforge").mkdir(parents=True, exist_ok=True)
            (out / ".archforge" / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
            client = DeepSeekClient.for_provider(params["provider"], params["key"], params["model"])
            agent = Agent(client, out, budget_usd=params["budget"], on_event=lambda k, d: events.put((k, d)))
            state["agent"] = agent
            agent.run(compact_spec(spec))
        except Exception as e:
            events.put(("crash", {"text": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"}))

    def start():
        p = {k: v[k].get() for k in ("provider", "model", "key", "doc", "view", "out", "budget", "fresh")}
        p["key"] = p["key"].strip()
        missing = [n for n, k in (("API key", "key"), ("documentation file", "doc"),
                                  ("views file", "view"), ("output folder", "out")) if not p[k]]
        if missing:
            messagebox.showwarning("Missing input", "Please provide: " + ", ".join(missing))
            return
        for k in ("doc", "view"):
            if not Path(p[k]).is_file():
                messagebox.showerror("File not found", p[k])
                return
        st = load_settings()
        st.update({k: p[k] for k in ("provider", "model", "doc", "view", "out", "budget")})
        keys = st.get("keys", {})
        if v["remember"].get():
            keys[p["provider"]] = p["key"]
        else:
            keys.pop(p["provider"], None)
        st["keys"] = keys
        save_settings(st)
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        set_plan([])
        run_btn.config(state="disabled")
        stop_btn.config(state="normal")
        v["status"].set("Starting…")
        t = threading.Thread(target=worker, args=(p,), daemon=True)
        state["thread"] = t
        t.start()

    def stop():
        if state["agent"]:
            state["agent"].cancel()
            write("Stop requested: the agent will stop after the current step.", "warn")
            stop_btn.config(state="disabled")

    run_btn.config(command=start)
    stop_btn.config(command=stop)
    root.after(100, pump)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
