# CLAUDE.md

This file provides guidance to Claude (claude.ai/code) when working with code in this repository.

## Commands

**Start the server (with hot reload):**
```bash
python run.py
# Opens at http://127.0.0.1:8000
# Auto-reloads on changes to app/ directory
# Disable reload: RELOAD=0 python run.py
```

**Run smoke tests (no LLM required):**
```bash
python tests/smoke.py
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**API docs:** visit `/docs` after starting the server.

## Configuration

Copy `.env.example` to `.env` and fill in at minimum:
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` — generator LLM (OpenAI-compatible; e.g. Aliyun qwen-plus)
- `JUDGE_BASE_URL/API_KEY/MODEL` — optional separate judge LLM; falls back to generator LLM if unset

Runtime config can also be changed without restart via the frontend's config dialog, which writes to `data/runtime_config.json` and overrides `.env` values in-memory.

Key env vars: `PORT` (default 8000), `MAX_CONCURRENCY` (default 5), `DATA_DIR` (default `./data`).

## Architecture

**Request flow:** `web/index.html` → FastAPI routes in `app/main.py` → service modules → SQLite via `app/store.py`

**`app/main.py`** — All FastAPI routes (~57KB). Single file for all HTTP endpoints + SSE streaming.

**`app/store.py`** — SQLite persistence layer. All DB reads/writes go here. Database file lives in `DATA_DIR`.

**`app/models.py`** — All Pydantic models (request bodies, DB entities, response shapes). Central source of truth for data structures.

**`app/config.py`** — Loads `Settings` from env at startup. Supports runtime override via `data/runtime_config.json` (no restart needed). Exposes `settings` singleton imported by other modules.

**`app/llm.py`** — Thin async HTTP client wrapping OpenAI-compatible `/chat/completions`. Used by generator and evaluator.

**`app/adapters.py`** — Adapters for calling the agent under test: `openai` (generic OpenAI-compatible), `bailian` (Alibaba), `coze` (Coze bot API).

**`app/generator.py`** — Dimension-driven test case generation. Takes an agent's `system_prompt`, calls LLM to analyze it, then generates cases across the 5 classic dimensions (alignment / boundary / industry / badcase / security).

**`app/pd_generator.py`** — Prompt-Debugger style generation. User inputs free-text "test points"; generates cases by P0–P6 priority levels.

**`app/dynamic_generator.py`** — Generates `dialogue_mode="dynamic"` test cases, where a `UserSimulator` drives multi-turn conversations at run time instead of using scripted turns.

**`app/user_simulator.py`** — LLM-powered user simulator. Used by `runner.py` for dynamic dialogue cases; drives conversation using `user_persona` + `user_goal` from the test case.

**`app/evaluator.py`** — Two-layer evaluation: (1) rule layer: keyword/length checks against `pass_criteria`; (2) LLM-as-Judge with G-Eval CoT + ensemble voting (`JudgeEnsembleConfig.n` calls, default 3). Final `passed = rule_ok AND judge_passed`.

**`app/runner.py`** — Async batch executor. Uses `asyncio.Semaphore` for concurrency control. Emits events to an in-memory event bus that SSE endpoint (`/api/runs/{id}/stream`) reads.

**`web/index.html`** — Single-page frontend. Tailwind CDN + vanilla JS. No build step.

## Key Data Models (`app/models.py`)

- **`AgentUnderTest`** — the system under test; holds `system_prompt`, `adapter`, `config`, `variables`, and cached `analysis`
- **`TestCase`** — one test scenario; has `dimension`, `turns` (scripted messages), or `user_persona`/`user_goal` (dynamic mode), plus `pass_criteria`
- **`TestRun`** / **`CaseResult`** — run tracking and per-case outcomes including `transcript`, `score`, `metrics`, `judge_runs`

## Test Dimensions

`alignment` · `boundary` · `industry` · `badcase` · `security` · `multi_turn` · `instruction_following` · `robustness` · `tone` · `factuality` · `format`
