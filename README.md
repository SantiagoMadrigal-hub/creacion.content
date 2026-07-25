# LAVOX — Automated Video Generation Pipeline

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Clean%20Architecture-green)](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
[![Async](https://img.shields.io/badge/Async-First-purple)](https://docs.python.org/3/library/asyncio.html)
[![Type Safety](https://img.shields.io/badge/Typing-mypy%20--strict-blue)](https://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/Tests-124%20%7C%2085%25%2B%20coverage-brightgreen)](https://pytest.org/)
[![Lint](https://img.shields.io/badge/Lint-ruff-orange)](https://docs.astral.sh/ruff/)
[![Package Manager](https://img.shields.io/badge/Package%20Manager-uv-fuchsia)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

> **Production-grade async pipeline**: Script → LLM Narrative Analysis → Semantic Stock Video Curation (Pexels) → Concurrent Download → FFmpeg Assembly → Final Video.

A refactor from prototype to **enterprise-ready Clean Architecture**: zero hardcoded secrets, fully async, strict typing, comprehensive test suite, and semantic exit codes.

---

## 🎯 Why This Project Exists (And What It Demonstrates)

This project started as a working prototype with API keys in source code, synchronous HTTP, `moviepy` overhead, and no tests. I rewrote it to **production standards** to demonstrate:

| Area | Before | After |
|------|--------|-------|
| **Architecture** | 7 coupled scripts | Clean Architecture (Domain / Application / Infrastructure / CLI) |
| **Secrets** | 3 hardcoded keys in `config.py` | `pydantic-settings` + `SecretStr`, `.env` gitignored, pre-commit secret scanning |
| **Concurrency** | Sequential `subprocess` + `requests` | `asyncio` + `httpx.AsyncClient` + bounded semaphores |
| **FFmpeg** | `moviepy` wrapper (slow, opaque) | Direct `asyncio.create_subprocess_exec` + `ffprobe` JSON |
| **Resilience** | Bare `try/except` | `tenacity` policies per-adapter, circuit-breaker-ready |
| **Testing** | None | 124 tests (unit + integration, `respx`/`pytest-mock`), 85%+ coverage gate |
| **Type Safety** | Optional hints | `mypy --strict` clean, `Protocol` ports, `TypedDict` DTOs |
| **CLI** | `print()` + manual args | `Typer` + `Rich`, semantic exit codes, `--dry-run`, progress bars |
| **Observability** | `print()` | `structlog` + `correlation_id` contextvars |

---

## 🏗️ Architecture

```mermaid
graph TD
    CLI[Typer CLI<br/>Composition Root] --> Orch[PipelineOrchestrator]
    Orch --> AnalyzeUC[AnalyzeScriptUseCase]
    Orch --> DownloadUC[DownloadClipsUseCase]
    Orch --> AssembleUC[AssembleVideoUseCase]

    AnalyzeUC --> SceneCurator[SceneCurator Domain Service]
    AnalyzeUC --> Script[Script Entity]
    AnalyzeUC --> Scene[Scene Entity]

    SceneCurator --> LLMPort[LLMPort Protocol]
    SceneCurator --> VideoSearchPort[VideoSearchPort Protocol]

    LLMPort --> Groq[GroqClient]
    LLMPort --> OpenAI[OpenAIClient]
    LLMPort --> Fallback[FallbackClient<br/>(Groq → OpenAI)]

    VideoSearchPort --> Pexels[PexelsSearcher]

    DownloadUC --> DownloaderPort[VideoDownloaderPort Protocol]
    DownloaderPort --> AsyncDL[AsyncDownloader<br/>Semaphore + gather]

    AssembleUC --> AssemblerPort[VideoAssemblerPort Protocol]
    AssembleUC --> AudioPort[AudioProviderPort Protocol]
    AssemblerPort --> FFmpeg[FFmpegAssembler<br/>concat demuxer]
    AudioPort --> FFprobe[LocalAudioProvider<br/>ffprobe JSON]

    Settings[Settings pydantic] -.configures.-> CLI
    Logger[structlog] -.observes.-> Orch
```

### Key Design Decisions

- **Domain owns zero external dependencies** — entities, ports (`Protocol`), services, and exceptions live in `domain/`. Swapping Groq→Anthropic or Pexels→Pixabay requires only new infrastructure adapters.
- **Single retry policy** — SDK internal retries disabled; all transient failures (LLM, Pexels, downloads) use `tenacity` with exponential backoff + jitter, configured via `Settings`.
- **Fallback as a first-class port** — `FallbackClient` implements `LLMPort` by composing two other `LLMPort`s. The domain sees one LLM; ordering is an infrastructure concern.
- **Checkpoint compatibility** — `Scene.to_dict() / Scene.from_dict()` use the exact same JSON schema as the original prototype, so existing checkpoints remain valid resumption points (no re-spending LLM/Pexels budget).
- **`ffprobe` replaces `moviepy`** — The original used `moviepy` only for audio duration and `ffmpeg` binary discovery. Both are now resolved via `ffprobe -show_format -print_format json`, eliminating a heavy dependency and avoiding fragile text parsing.
- **Concurrency control** — `AsyncDownloader.download_many` uses `asyncio.Semaphore` + `asyncio.gather(..., return_exceptions=True)` so one failure never cancels the rest.

---

## 📋 Requirements

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (package/environment manager)
- `ffmpeg` and `ffprobe` in `PATH` (or configurable via `LAVOX_FFMPEG__*`)
- API keys for **Groq** and/or **OpenAI**, and **Pexels**

---

## ⚡ Installation

```bash
uv sync                       # installs dependencies + dev tools in .venv
cp .env.example .env          # fill in your own keys
```

---

## ⚙️ Configuration

All configuration lives in `src/lavox/settings.py` (`pydantic-settings`), with precedence:

```
defaults  →  .env  →  real env vars  →  CLI flags
```

Variables use the `LAVOX_` prefix, and `__` (double underscore) for nesting:
- `LAVOX_LLM__GROQ_API_KEY`
- `LAVOX_PIPELINE__RELEVANCE_THRESHOLD`
- etc.

See [`.env.example`](.env.example) for the complete, commented list.

API keys are exposed as `pydantic.SecretStr` — they never appear in `repr()`, logs, or tracebacks.

---

## 🚀 Usage

```bash
# Full pipeline
uv run lavox-pipeline run --guion guion.txt

# Individual phases
uv run lavox-pipeline analyze --guion guion.txt
uv run lavox-pipeline download --workers 8
uv run lavox-pipeline assemble --audio audios/narracion.mp3

# Validate config & show plan without calling external providers
uv run lavox-pipeline run --guion guion.txt --dry-run

# Standalone entry points (installed via pyproject.toml)
uv run lavox-analyze --guion guion.txt
uv run lavox-download --workers 8
uv run lavox-assemble --audio audios/narracion.mp3
```

### Main Flags

| Flag | Commands | Description |
|------|----------|-------------|
| `--guion` | `analyze`, `run` | Input script path |
| `--output` | all | Output path (checkpoint, clips dir, or final video) |
| `--workers` | `download`, `run` | Max concurrent downloads |
| `--max-relevance` | `analyze`, `run` | Relevance threshold (0–100) to accept a clip |
| `--resume` / `--no-resume` | `analyze`, `run` | Resume from existing checkpoint (default: yes) |
| `--dry-run` | all | Validate config/inputs without external calls |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Full success |
| `1` | Config error (missing file, API key, etc.) |
| `2` | LLM provider error |
| `3` | Download error (catastrophic: no downloads succeeded) |
| `4` | Assembly error (FFmpeg) |
| `5` | Partial success — video produced but degraded (some scenes without clip, or individual download failures) |

---

## 🛠️ Development

```bash
uv sync --group dev
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src
uv run pytest --cov=src --cov-fail-under=85
uv build
uv run pre-commit install   # optional: runs hooks on every commit
```

---

## 🔧 Troubleshooting

**`ConfigError: No hay ninguna API key de LLM configurada`**  
Missing `LAVOX_LLM__GROQ_API_KEY` and/or `LAVOX_LLM__OPENAI_API_KEY` in `.env`. At least one required.

**`AssemblyError: No se encontró el ejecutable 'ffmpeg'`**  
`ffmpeg`/`ffprobe` not in `PATH`. Install (`apt install ffmpeg` on Debian/Ubuntu) or set `LAVOX_FFMPEG__BINARY` / `LAVOX_FFMPEG__PROBE_BINARY` to absolute paths.

**Pipeline restarts from scratch instead of resuming**  
Ensure `--resume` is active (default) and `LAVOX_SCENES_OUTPUT_PATH` points to the same checkpoint used previously.

**Partial output (exit code 5) with many scenes missing clips**  
Lower `--max-relevance` to a more permissive value, or verify `LAVOX_PIPELINE__CONTEXTO_NARRATIVO` accurately describes your script's topic — the LLM uses this to generate `elementos_clave` for search queries.

**Constant Pexels rate limits (429)**  
Reduce `LAVOX_PIPELINE__MAX_DOWNLOAD_CONCURRENCY` and/or `LAVOX_PEXELS__PER_PAGE`; Pexels enforces per-minute limits based on your plan.

---

## 🤝 Contributing

1. `uv sync --group dev && uv run pre-commit install`
2. Before PR: `ruff check . && ruff format --check . && mypy --strict src && pytest --cov=src --cov-fail-under=85`
3. Follow Clean Architecture: domain imports no infrastructure; use cases depend only on ports (`Protocol`), never concrete implementations.
4. New or modified LLM prompts belong in the layer that already owns them (`SceneCurator` or `AnalyzeScriptUseCase`), not in infrastructure.

---

## 📜 Changelog

See [`CHANGELOG.md`](CHANGELOG.md).

---

## 📄 License

MIT.