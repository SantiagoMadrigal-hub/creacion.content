# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y este proyecto usa [Versionado Semántico](https://semver.org/lang/es/).

## [0.1.0] - Refactor a Clean Architecture

Reescritura completa del prototipo original (`config.py`, `ejercicio1.py`,
`curador_ia.py`, `descargar_videos.py`, `ensamblar_video.py`,
`crear_contenido.py`, `llm_cliente.py`, `enriquecedor_pexels.py`) a un
paquete `lavox` con Clean Architecture, tipado estricto, tests y CLI
profesional. El comportamiento observable del pipeline se preserva
intencionalmente (mismos prompts, mismo esquema de checkpoint, misma
lógica de curación); lo que cambia es la estructura, la seguridad y la
robustez.

### 🔴 Seguridad

- **Eliminadas las tres API keys hardcodeadas** (Groq, Pexels, OpenAI) que
  vivían en texto plano en `config.py`. Ahora se cargan exclusivamente
  desde variables de entorno (`pydantic-settings`, `SecretStr`), nunca
  aparecen en logs/reprs/tracebacks. **Acción manual requerida**: rotar las
  tres keys expuestas (ver README, sección Seguridad).
- Agregado `.gitignore` robusto (`.env`, artefactos de video/audio,
  cachés de herramientas) y un hook de pre-commit que bloquea patrones de
  API keys hardcodeadas (`gsk_...`, `sk-...`) antes de cada commit.

### Added

- Paquete `src/lavox` con Clean Architecture: `domain/` (entidades, puertos
  `Protocol`, servicio `SceneCurator`, jerarquía de excepciones),
  `application/` (casos de uso + `PipelineOrchestrator`),
  `infrastructure/` (adaptadores concretos), `cli/` (Typer + Rich).
- `Settings` tipado (`pydantic-settings`) con precedencia
  default → `.env` → env vars → flags de CLI, y validación de tipos al
  cargar.
- Reintentos con `tenacity` (backoff exponencial + jitter) para LLM
  (Groq/OpenAI), Pexels y descargas; clasificación de errores recuperables
  basada en los tipos de excepción reales de cada SDK, no en matching de
  texto.
- `FallbackClient`: separa la política "Groq primero, OpenAI de respaldo"
  de la implementación de cada proveedor.
- Descargas concurrentes acotadas (`asyncio.Semaphore` +
  `asyncio.gather(..., return_exceptions=True)`) vía
  `AsyncDownloader.download_many`, con reanudación y limpieza automática
  de archivos corruptos/incompletos.
- `FFmpegAssembler` async (`asyncio.create_subprocess_exec`) con timeouts
  configurables y limpieza garantizada del directorio temporal.
- Logging estructurado (`structlog`): consola legible en desarrollo, JSON
  en producción, `correlation_id` propagado por `contextvars`.
- CLI (`lavox-pipeline`) con subcomandos `analyze` / `download` /
  `assemble` / `run`, barras de progreso y tablas de resumen (`rich`),
  modo `--dry-run` y códigos de salida semánticos (0-5).
- Scripts de entrada standalone: `lavox-analyze`, `lavox-download`,
  `lavox-assemble`.
- Suite de tests (`pytest`, `pytest-asyncio`, `respx`, `pytest-mock`):
  90%+ de cobertura en `src/lavox`, incluyendo tests de integración del
  pipeline completo y tests de `FFmpegAssembler`/`LocalAudioProvider`
  contra binarios reales de `ffmpeg`/`ffprobe` sobre clips sintéticos.
- `pyproject.toml` completo (`ruff`, `mypy --strict`, `pytest`,
  `coverage`), `.pre-commit-config.yaml`, `Dockerfile` +
  `docker-compose.yml`, scripts `scripts/rotate_keys.sh` y
  `scripts/run_local.sh`.

### Changed

- El checkpoint de escenas mantiene el **mismo esquema JSON** que la
  versión original (`tema_principal`, `emocion_tono`, `tipo_escena`,
  `elementos_clave`, `narracion`, `numero`, `variaciones_intentadas`,
  `clip_seleccionado`, `reintentos`): un `escenas_con_videos.json` ya
  generado sigue siendo un checkpoint de reanudación válido.
- El "contexto narrativo" que se le da al LLM al analizar cada escena
  (antes hardcodeado como `"Documental sobre palabras en inglés con
  orígenes engañosos"` dentro del prompt) ahora es configurable vía
  `LAVOX_PIPELINE__CONTEXTO_NARRATIVO`, con ese mismo texto como valor por
  defecto para no cambiar el comportamiento out-of-the-box.
- La duración de clips de video y del audio se obtiene con
  `ffprobe -show_format -print_format json` en vez de: (a) `moviepy`
  (usado antes solo para leer la duración del audio) y (b) una expresión
  regular sobre la salida de texto de `ffmpeg` (usada antes para la
  duración de cada clip). Se elimina la dependencia de `moviepy`.
- `crear_contenido.py` (que lanzaba tres scripts por separado vía
  `subprocess`) se reemplaza por `PipelineOrchestrator`, que llama las tres
  fases directamente en el mismo proceso.

### Deprecated / Removed

- **`enriquecedor_pexels.py` eliminado.** Estaba deprecado por
  `curador_ia.py` + `ejercicio1.py` (que sí evalúan relevancia semántica;
  el enriquecedor solo tomaba el primer resultado de Pexels sin evaluar
  nada) y ya no se usaba en el flujo principal del pipeline.

### Fixed

- Se aisló la invocación de los callbacks de progreso (`on_complete`,
  `on_scene_processed`) para que una excepción dentro de un callback nunca
  corrompa el resultado real de una descarga u otra operación (encontrado
  durante el desarrollo de los tests de `AsyncDownloader`).

### Known limitations / próximos pasos sugeridos

- No se implementó circuit breaker (marcado como opcional en el alcance
  original); con los reintentos + fallback actuales el pipeline ya es
  resiliente a fallos transitorios, pero un circuit breaker evitaría
  seguir golpeando un proveedor caído durante minutos.
- No hay métricas exportadas a un backend tipo Prometheus, solo logging
  estructurado con los campos clave (latencia, tokens no expuestos por los
  SDKs, clips evaluados, relevancia, tiempos de descarga/ensamblaje).
- `cli/main.py` tiene menor cobertura de tests que el dominio/aplicación
  (las funciones de *composition root* que construyen infraestructura real
  requieren credenciales para probarse de extremo a extremo); la lógica de
  negocio que sí importa está cubierta por los tests de dominio/aplicación.
