# Configuration

## `.env`

See [`.env.example`](../.env.example). Values are **starting defaults**: after the first start most settings live in the database (admin panel → Settings) and `.env` no longer overwrites them.

| variable | meaning |
|---|---|
| `SYMPOSIUM_HOST` / `PORT` / `UI_PORT` | bind address, API port, UI port (`off` — API only) |
| `SYMPOSIUM_DATA_DIR` | database, workspaces, RAG files, logs |
| `SYMPOSIUM_ADMIN_PASSWORD` | initial password of `admin` |
| `SYMPOSIUM_BOOT_*` | the first LLM profile (name, base URL, key, model, limiter preset) |
| `SYMPOSIUM_OLLAMA_URL`, `_EMBEDDING_MODEL`, `_EMBEDDING_DIMS` | library embeddings |
| `SYMPOSIUM_SEARXNG_URL`, `_WEB_SEARCH_ENGINES` | web search |

## LLM profiles and the limiter

Profiles (admin → LLM profiles) hold the endpoint, key, model, the **reasoning master switch** and the limiter policy. The limiter is off by default; presets:

| preset | limits |
|---|---|
| `openrouter_free` | 20 req/min, 50 req/day, 1 concurrent |
| `openrouter_free_credits` | 20 req/min, 1000 req/day, 1 concurrent |
| `mistral_free` | 1 req/s, 500k tokens/min, 1B tokens/month |
| `serial_polite` | 1 concurrent, 0.5 s pause |

Sources: <https://openrouter.ai/docs/api-reference/limits>, <https://help.mistral.ai/en/articles/225174-what-are-the-limits-of-the-free-tier>. Limits change — edit them to match your account.

If the profile's reasoning is **off**, no character can use reasoning, whatever its own settings say. Effort (low/medium/high) is a per-character setting.

## Key settings (admin → Settings)

`strict_state`, `summary_mode` (`digest`|`llm`), `stall_rounds`, `default_max_rounds`, `compression_threshold`, `subagent_calls_per_turn`, `auto_recovery` / `auto_recovery_max`, `confirm_destructive`, `web_fetch_max_bytes`, `web_fetch_allow_private`, `sandbox_max_timeout_sec`, `workspace_mem_limit_mb`, `web_search_*`, `ollama_url`, `embedding_model`.

## Workspaces

Each symposium has a workspace under `data/workspaces/<id>`: `uploads/` (files you attach), `downloads/` (fetched files), `artifacts/`, `subagent_logs/`, and a private `.venv` created on first use (`uv venv` if available, else `python -m venv`). The terminal tab opens a shell in the same directory with the same environment.
