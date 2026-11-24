# ArchForge — a simplified Claude-Code-style coding agent

ArchForge is a desktop coding agent that reads **architecture documentation** and **UML views**
(PlantUML in Markdown) and autonomously generates a **complete, tested desktop application**:
source code, tests, `requirements.txt`, `README.md` and a `Dockerfile`.

It is my submission for Evaluation Task 1 (implementation of a simplified code agent).
The provided sample input (the *Space Fractions* architecture) is in `examples/inputs/`.

| | |
|---|---|
| Deliverable | Windows desktop app, single double-click `ArchForge.exe` (no Python needed) |
| LLM | OpenAI-compatible API: **DeepSeek** (`deepseek-v4-pro`, as required) or **Gemini** |
| Agent output | Python 3 + tkinter desktop app, pytest suite, README, requirements, Dockerfile |
| Agent's own tests | 34 pytest tests (offline, scripted fake LLM + mock HTTP server) |

> **Video demo:** _YouTube link here_ · **Note on the LLM provider:** see [Provider note](#provider-note).

---

## 1. How it works

```
 Architecture_Documentation.md ─┐
                                ├─► Preprocessor ─► spec.json (+ compact spec, ~2.5k tokens)
 Architecture_View.md (UML) ────┘        (no LLM, deterministic)
                                                     │
                                                     ▼
                ┌──────────────────────── Agent loop ────────────────────────┐
                │  system prompt (static, first → prefix-cache friendly)     │
                │  user: compact spec                                        │
                │    ┌─► LLM chooses tool calls ──► tools run in a sandbox ─┐│
                │    └──────────── tool results / errors fed back ◄────────┘│
                │  finish ─► verification: pytest + quality checks           │
                │            fail → repair round (max 4) │ pass → done       │
                └────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
                         output/ project + .archforge/{report.json, transcript.jsonl, cost}
```

### Components (`archforge/`)

| Module | Responsibility |
|---|---|
| `preprocess.py` | Parses the documentation (sections, tables, code blocks, FR/NFR/ASR requirements) and all 13 PlantUML diagrams (classes, use cases, actors, states and transitions, activity steps, sequence messages, packages, nodes) into structured JSON. A compact version is sent to the model to save tokens. |
| `agent.py` | The agent loop, verification on `finish`, repair rounds, nudging when the model replies without a tool call, model fallback, resume, logging. |
| `tools.py` | Sandboxed tools: `update_plan`, `write_file`, `edit_file`, `read_file`, `list_dir`, `run_tests`, `finish`. |
| `checks.py` | Static quality checks: import graph from `main.py` (finds dead/orphan modules) and a real import check of every reachable module. |
| `llm.py` | Minimal OpenAI-compatible client (stdlib only): retries with backoff, clear quota/busy errors, provider presets. |
| `cost.py` | Token and USD accounting (cache-hit / cache-miss / output prices, DeepSeek peak/off-peak) and a hard budget stop. |
| `prompts.py` | System prompt (platform rules, UML-to-code mapping rules, UI quality bar, work procedure) and repair messages. |
| `gui.py` | tkinter desktop app: API key, provider/model, input files, output folder, budget, Run/Stop, live plan, colour-coded log, cost counter. |

## 2. Design decisions (mapped to Claude Code concepts)

I studied the reference repositories ([how-claude-code-works](https://github.com/Windy3f3f3f3f/how-claude-code-works),
[claw-code](https://github.com/ultraworkers/claw-code), [claude-code](https://github.com/anthropics/claude-code))
and kept the mechanisms that matter for a single-agent code generator:

1. **Query loop with tool use.** The model returns tool calls; results go back as `tool` messages; the loop continues until `finish` or a limit. Several independent `write_file` calls may be batched in one turn to save requests.
2. **Tool schema + safety flags.** Every tool has a JSON schema and a read-only flag. All paths are resolved inside the output folder; `../` or absolute paths are rejected. Commands are limited to running pytest, with a timeout.
3. **Errors are data, not crashes.** Bad JSON arguments, unknown tools, missing files and failing tests are returned to the model as `ERROR: …` tool results so it can fix them.
4. **Verification before completion.** When the model calls `finish`, ArchForge runs the tests **and** checks the result against the architecture:
   - `main.py`, `README.md`, `requirements.txt` exist; at least 3 test files;
   - every **use case** from the UML appears in the **UI code** (not just as a model method);
   - every **state** from the state diagram is implemented;
   - no **orphan modules** (code not reachable from `main.py`);
   - every reachable module **imports** without error.
   If anything fails, the model gets a precise repair message (up to 4 rounds).
5. **Context and cost control.** Deterministic preprocessing instead of sending raw Markdown; a static system prompt placed first (DeepSeek prefix cache: cache hits are ~30× cheaper); long tool output truncated; live cost tracking with a hard budget (default $1.00).
6. **Planning.** The model first writes a plan with `update_plan`; the GUI shows it and the agent updates statuses as files are written and when verification passes.
7. **Robustness for free-tier APIs.** Retries on 429/5xx; on a daily-quota error or repeated 503 the agent switches to the next model in a fallback list; `--resume` continues from files already written.

**Deliberately not implemented** (out of scope for a one-week task): sub-agents/multi-agent orchestration, history compaction (runs fit in the context window), an interactive permission dialog (the sandbox replaces it).

### Mapping the architecture to a desktop app

The provided documentation describes a cloud microservice system (Kubernetes, PostgreSQL, Redis…). Because the task requires a **desktop** application, the agent is instructed to map each component to a Python module (Game, Question, User/Admin, persistence) and to explain this mapping in the generated README. Use cases become menu buttons (Play Game, View Score, View Help, Update Questions), and the state diagram (Playing → Paused → GameOver) is implemented and reachable from the UI.

## 3. Usage

### Windows (no Python required)
1. Double-click `ArchForge.exe`.
2. Choose provider and model, paste the API key, and tick "remember key" if you want.
3. The sample inputs are pre-filled; choose an output folder.
4. Click **▶ Generate project**. When it reports `SUCCESS`, open the output folder and run `python main.py` (or open the generated README).

### From source (Linux/macOS/Windows)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m archforge                                    # opens the desktop app
# or command line:
export DEEPSEEK_API_KEY=...                            # or put it in a .env file
python -m archforge run --provider deepseek --model deepseek-v4-pro \
  --doc examples/inputs/Architecture_Documentation.md \
  --view examples/inputs/Architecture_View.md --out output/space_fractions --fresh
```
Options: `--provider deepseek|gemini`, `--model`, `--budget 1.0`, `--max-steps 60`, `--fresh` (otherwise it resumes).

### Build the .exe
On Windows with Python 3.10+: run `build_windows.bat` → `dist\ArchForge.exe`
(PyInstaller one-file, windowed; pytest is bundled so the agent can run the generated tests without a Python install).

### Run ArchForge's own tests
```bash
python -m pytest -q        # 34 tests, offline (fake LLM + local mock HTTP server)
```

## 4. Outputs and logs
Each run writes the generated project plus `.archforge/`:
- `report.json`: status, files, steps, test result, quality issues, tokens and cost
- `transcript.jsonl`: the full conversation (for inspection and debugging)
- `cost_per_call.json`: per-call token usage and cost
- `spec.json`: the structured architecture spec given to the agent

Example result (Gemini free tier): `SUCCESS | tests passed | 10 files | 16 steps | ~100 s`, including two
automatic repair rounds in which the verifier reported the missing `requirements.txt`, too few test files,
and the "Update Questions" / "View Help" use cases missing from the UI, and the agent fixed them.

## <a id="provider-note"></a>5. Provider note
The task asks for `deepseek-v4-pro` via the official API, and ArchForge supports it directly
(`--provider deepseek`, default prices and peak/off-peak cost accounting included). From Pakistan, my
payments to the DeepSeek platform failed repeatedly, so I developed and tested with the **Gemini free tier**
through the same OpenAI-compatible client. Switching back is one setting; no code changes are needed.
The cost guard is designed around DeepSeek pricing (budget $1.00 per run).

## 6. Limitations and future work
- Free-tier Gemini models can be slow or overloaded (HTTP 503), so a run may take several minutes.
- The quality checks are static (text and import based). A next step is a headless UI smoke test (e.g. `xvfb` and simulated clicks).
- No context compaction yet; very large specifications would need it.
- Only Python/tkinter targets are supported by the prompt today.

## 7. Acknowledgement
I built this project with the help of an AI coding assistant (Claude), which I used as a pair programmer.
I set the requirements and made the design decisions (provider fallback, verification against the UML,
Windows packaging), ran and debugged every step on my own machines (Ubuntu and Windows), and I can explain
every part of the code.

— Zahida Parveen · github.com/RaiZahida
