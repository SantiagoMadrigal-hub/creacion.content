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
```

### Key Design Decisions

- **Domain owns zero external dependencies** — entities, ports (`Protocol`), services, and exceptions live in `domain/`. Swapping Groq→Anthropic or Pexels→Pixabay requires only new infrastructure adapters.
- **Single retry policy** — SDK internal retries disabled; all transient failures (LLM, Pexels, downloads) use `tenacity` with exponential backoff + jitter, configured via `Settings`.
- **Fallback as a first-class port** — `FallbackClient` implements `LLMPort` by composing two other `LLMPort`s. The domain sees one LLM; ordering is an infrastructure concern.
- **Checkpoint compatibility** — `Scene.to_dict() / from_dict()` serialize to the exact JSON schema the original prototype produced. Existing checkpoints resume without re-spending LLM/Pexels quota.
- **`ffprobe` over `moviepy`** — Removed a heavy dependency; duration extraction via `ffprobe -print_format json -show_format` is faster and more reliable.
- **Bounded concurrency** — `AsyncDownloader.download_many` uses `asyncio.Semaphore(max_concurrency)` + `gather(return_exceptions=True)` so one failure never cancels the rest.

---

## 🛠️ Tech Stack

| Layer | Choices |
|-------|---------|
| **Language** | Python 3.11+ (modern `asyncio`, `Self`, `TypedDict`, `match`) |
| **Async HTTP** | `httpx.AsyncClient` (connection pooling, timeouts, redirects) |
| **LLM Providers** | `groq` SDK, `openai` SDK — wrapped behind `LLMPort` |
| **Stock Video** | Pexels Video API (`httpx` + `pydantic` response models) |
| **Media Processing** | `ffmpeg` / `ffprobe` via `asyncio.subprocess` |
| **Config** | `pydantic-settings` (env prefix `LAVOX_`, nested `__`, `SecretStr`) |
| **Logging** | `structlog` (console dev / JSON prod) + `contextvars` correlation IDs |
| **CLI** | `Typer` + `Rich` (tables, progress bars, semantic exit codes) |
| **Testing** | `pytest` + `pytest-asyncio` + `respx` (HTTP mock) + `pytest-mock` |
| **Quality Gates** | `ruff` (lint+format), `mypy --strict`, `pytest --cov-fail-under=85` |
| **Packaging** | `pyproject.toml` (hatchling), `uv` lockfile, entry points |
| **CI/CD Ready** | `pre-commit` (ruff, mypy, secret scan), `Dockerfile` multi-stage |

---

## 📁 Project Structure

```
src/lavox/
├── cli/                    # Typer app — composition root
│   └── main.py             # lavox-pipeline, lavox-analyze, lavox-download, lavox-assemble
├── domain/                 # Pure Python, zero external deps
│   ├── entities/           # Scene, Clip, Script (dataclasses + serialization)
│   ├── ports/              # Protocols: LLMPort, VideoSearchPort, VideoDownloaderPort, VideoAssemblerPort, AudioProviderPort
│   ├── services/           # SceneCurator (semantic curation logic)
│   └── exceptions.py       # LavoxError → ConfigError, LLMError, PexelsError, DownloadError, AssemblyError, PartialPipelineError
├── application/            # Use cases + orchestrator
│   ├── use_cases/          # AnalyzeScript, DownloadClips, AssembleVideo
│   └── pipeline/           # PipelineOrchestrator (runs all three)
├── infrastructure/         # Concrete adapters
│   ├── llm/                # GroqClient, OpenAIClient, FallbackClient, _parsing.py (tolerant JSON extraction)
│   ├── video_search/       # PexelsSearcher (pydantic models + tenacity)
│   ├── download/           # AsyncDownloader (semaphore, resume, validation)
│   ├── video/              # FFmpegAssembler (letterbox, loop, concat demuxer, audio mux)
│   ├── audio/              # LocalAudioProvider (ffprobe JSON)
│   ├── _retry.py           # Shared tenacity policy (429/5xx + transport errors)
│   └── _ffprobe.py         # Shared ffprobe duration extraction
├── settings.py             # Pydantic Settings (validated, SecretStr keys)
└── logging_config.py       # structlog setup + correlation_id binding
```

---

## ⚡ Quick Start

```bash
# 1. Clone & install (uv is ~10x faster than pip)
git clone https://github.com/SantiagoMadrigal-hub/creacion.content.git
cd creacion.content
uv sync --all-extras        # creates .venv, installs runtime + dev deps

# 2. Configure secrets (never committed)
cp .env.example .env
# Edit .env with your keys:
# LAVOX_LLM__GROQ_API_KEY=gsk_...
# LAVOX_LLM__OPENAI_API_KEY=sk-...   # optional fallback
# LAVOX_PEXELS__API_KEY=...

# 3. Add narration audio
mkdir -p audios
# place your file at audios/vozenoff_completa.mp3 (or set LAVOX_AUDIO_PATH)

# 4. Run the pipeline
uv run lavox-pipeline run --guion guion.txt
# Output: video_final_definitivo.mp4
```

### Dry-run (validate config without calling APIs)

```bash
uv run lavox-pipeline run --guion guion.txt --dry-run
```

### Step-by-step (useful for debugging)

```bash
uv run lavox-pipeline analyze --guion guion.txt    # → escenas_con_videos.json
uv run lavox-pipeline download                      # → videos/escena_*.mp4
uv run lavox-pipeline assemble                      # → video_final_definitivo.mp4
```

---

## 🧪 Development Workflow

```bash
# Install dev tools + pre-commit hooks
uv sync --group dev
uv run pre-commit install

# Quality gates (must pass before PR)
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src
uv run pytest --cov=src --cov-fail-under=85

# Build distributable
uv build
```

**Test breakdown:** 124 tests (unit + integration), mocked external APIs via `respx`, fixtures for Pexels responses, LLM outputs, and sample scripts.

---

## 🐳 Docker (Production-Ready)

```bash
# Build image (multi-stage, non-root, ffmpeg included)
docker build -t lavox -f docker/Dockerfile .

# Run with local files mounted
docker run --rm \
  --env-file .env \
  -v $(pwd)/guion.txt:/home/lavox/workspace/guion.txt:ro \
  -v $(pwd)/audios:/home/lavox/workspace/audios:ro \
  -v $(pwd)/videos:/home/lavox/workspace/videos \
  -v $(pwd)/output:/home/lavox/workspace/output \
  lavox run --guion guion.txt --output output/video_final.mp4
```

---

## 🔐 Security Posture

- **Zero secrets in source** — `.env` in `.gitignore`, `SecretStr` prevents accidental logging.
- **Pre-commit secret scanner** — blocks commits matching `gsk_*` or `sk-*` patterns.
- **GitHub Push Protection** — repo-level secret scanning enabled.
- **Dependency scanning** — `uv` lockfile + `pip-audit` in CI (add to workflow).

> **Note**: The original prototype had 3 live API keys hardcoded. They were rotated before this repo was published. If you forked the old version, **rotate your keys immediately**.

---

## 📊 Exit Codes (Automation-Friendly)

| Code | Meaning |
|------|---------|
| `0` | Full success |
| `1` | Config error (missing file, key, etc.) |
| `2` | LLM provider failure (non-retryable) |
| `3` | Download catastrophic (zero successful downloads) |
| `4` | FFmpeg assembly failure |
| `5` | **Partial success** — video produced but degraded (some scenes missing clips / some downloads failed) |

---

## 🎓 What This Demonstrates to Employers

| Competency | Evidence in This Repo |
|------------|----------------------|
| **System Design** | Clean Architecture, dependency inversion via `Protocol`, composition root pattern |
| **Async Python** | `asyncio`/`httpx`/`tenacity` patterns, bounded concurrency, proper exception handling |
| **API Integration** | Multiple providers (Groq, OpenAI, Pexels) with fallback, retry, timeout policies |
| **Media Processing** | Direct FFmpeg orchestration (letterbox, loop-trim, concat demuxer, audio mux) |
| **Testing Discipline** | 124 tests, mocks at port boundaries, integration test with full pipeline mock |
| **Type Safety** | `mypy --strict` clean, `Protocol`, `TypedDict`, generics, no `Any` leakage |
| **Developer Experience** | `Typer`/`Rich` CLI, `--dry-run`, semantic exits, progress bars, structured logging |
| **Packaging & Tooling** | `pyproject.toml`, `uv`, `ruff`, `pre-commit`, `Dockerfile`, `hatchling` build |
| **Security Awareness** | `SecretStr`, gitignore, pre-commit secret scan, push protection, key rotation docs |

---

## 📝 License

MIT — see [`LICENSE`](LICENSE).

---

## 👤 Author

**Santiago Madrigal**  
Full-Stack / Backend Developer — Python, TypeScript, Cloud, Clean Architecture  
[GitHub](https://github.com/SantiagoMadrigal-hub) • [LinkedIn](https://linkedin.com/in/santiago-madrigal)

---

> *This project is part of my portfolio demonstrating production-grade Python engineering. Feedback and questions welcome via Issues.*