"""CLI de LAVOX: interfaz de línea de comandos basada en Typer.

Expone cuatro subcomandos (``analyze``, ``download``, ``assemble``, ``run``)
sobre la app principal ``lavox-pipeline``, además de tres scripts standalone
equivalentes (``lavox-analyze``, ``lavox-download``, ``lavox-assemble``)
registrados en ``pyproject.toml``.

Esta es la única capa que conoce tanto los casos de uso de aplicación como
las implementaciones concretas de infraestructura: actúa como *composition
root*, construyendo las implementaciones a partir de :class:`Settings` e
inyectándolas en los casos de uso.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from enum import IntEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from lavox.application.pipeline.orchestrator import PipelineOrchestrator, PipelineResult
from lavox.application.use_cases.analyze_script import AnalyzeScriptUseCase
from lavox.application.use_cases.assemble_video import AssembleVideoUseCase
from lavox.application.use_cases.download_clips import DownloadClipsUseCase, DownloadSummary
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import AssemblyError, ConfigError, DownloadError, LavoxError, LLMError
from lavox.domain.ports.llm_port import LLMPort
from lavox.domain.services.scene_curator import SceneCurator
from lavox.infrastructure.audio.local_audio_provider import LocalAudioProvider
from lavox.infrastructure.download.async_downloader import AsyncDownloader
from lavox.infrastructure.llm.fallback_client import FallbackClient
from lavox.infrastructure.llm.groq_client import GroqClient
from lavox.infrastructure.llm.openai_client import OpenAIClient
from lavox.infrastructure.video.ffmpeg_assembler import FFmpegAssembler
from lavox.infrastructure.video_search.pexels_searcher import PexelsSearcher
from lavox.logging_config import bind_correlation_id, configure_logging
from lavox.settings import Settings, get_settings

__all__ = ["analyze_entrypoint", "app", "assemble_entrypoint", "download_entrypoint"]

console = Console()
app = typer.Typer(
    name="lavox-pipeline",
    help="Pipeline de creación de video: guion -> LLM -> Pexels -> FFmpeg.",
    no_args_is_help=True,
)


class ExitCode(IntEnum):
    """Códigos de salida semánticos de la CLI."""

    OK = 0
    CONFIG_ERROR = 1
    LLM_ERROR = 2
    DOWNLOAD_ERROR = 3
    ASSEMBLY_ERROR = 4
    PARTIAL = 5


# ---------------------------------------------------------------------------
# Composition root: construcción de Settings e infraestructura
# ---------------------------------------------------------------------------


def _load_settings(
    *,
    guion: Path | None = None,
    scenes: Path | None = None,
    clips_dir: Path | None = None,
    audio: Path | None = None,
    output: Path | None = None,
    max_relevance: int | None = None,
    workers: int | None = None,
) -> Settings:
    overrides: dict[str, object] = {}
    if guion is not None:
        overrides["script_path"] = guion
    if scenes is not None:
        overrides["scenes_output_path"] = scenes
    if clips_dir is not None:
        overrides["clips_dir"] = clips_dir
    if audio is not None:
        overrides["audio_path"] = audio
    if output is not None:
        overrides["final_output_path"] = output

    pipeline_overrides: dict[str, object] = {}
    if max_relevance is not None:
        pipeline_overrides["relevance_threshold"] = max_relevance
    if workers is not None:
        pipeline_overrides["max_download_concurrency"] = workers
    if pipeline_overrides:
        overrides["pipeline"] = pipeline_overrides

    settings = get_settings(**overrides)
    configure_logging(log_level=settings.log_level, json_format=settings.log_format == "json")
    bind_correlation_id()
    return settings


def _build_llm(settings: Settings) -> LLMPort:
    settings.require_llm_keys()
    groq_client: GroqClient | None = None
    openai_client: OpenAIClient | None = None

    if settings.llm.groq_api_key is not None:
        groq_client = GroqClient(
            settings.llm.groq_api_key.get_secret_value(),
            model=settings.llm.groq_model,
            max_attempts=settings.llm.max_retries,
            timeout=settings.llm.request_timeout,
        )
    if settings.llm.openai_api_key is not None:
        openai_client = OpenAIClient(
            settings.llm.openai_api_key.get_secret_value(),
            model=settings.llm.openai_model,
            max_attempts=settings.llm.max_retries,
            timeout=settings.llm.request_timeout,
        )

    if groq_client is not None:
        return FallbackClient(groq_client, openai_client)
    if openai_client is not None:
        return FallbackClient(openai_client)
    raise ConfigError("No hay ninguna API key de LLM configurada.")  # pragma: no cover


async def _build_video_search(settings: Settings, stack: AsyncExitStack) -> PexelsSearcher:
    settings.require_pexels_key()
    if settings.pexels.api_key is None:
        raise ConfigError("No hay API key de Pexels configurada.")  # pragma: no cover
    searcher = PexelsSearcher(
        settings.pexels.api_key.get_secret_value(),
        base_url=settings.pexels.base_url,
        min_width=settings.pexels.min_width,
        max_attempts=settings.pexels.max_retries,
        timeout=settings.pexels.request_timeout,
    )
    stack.push_async_callback(searcher.aclose)
    return searcher


async def _build_downloader(settings: Settings, stack: AsyncExitStack) -> AsyncDownloader:
    downloader = AsyncDownloader(
        max_attempts=settings.pipeline.download_max_retries,
        timeout=settings.pipeline.download_timeout,
    )
    stack.push_async_callback(downloader.aclose)
    return downloader


def _build_scene_curator(
    settings: Settings, llm: LLMPort, video_search: PexelsSearcher
) -> SceneCurator:
    return SceneCurator(
        llm,
        video_search,
        relevance_threshold=settings.pipeline.relevance_threshold,
        max_attempts=settings.pipeline.max_curation_attempts,
    )


def _build_assembler(settings: Settings) -> FFmpegAssembler:
    return FFmpegAssembler(
        binary=settings.ffmpeg.binary,
        probe_binary=settings.ffmpeg.probe_binary,
        resolution_width=settings.ffmpeg.resolution_width,
        resolution_height=settings.ffmpeg.resolution_height,
        preset=settings.ffmpeg.preset,
        crf=settings.ffmpeg.crf,
        process_timeout=settings.ffmpeg.process_timeout,
    )


# ---------------------------------------------------------------------------
# Runners asíncronos (un `asyncio.run` por comando de la CLI)
# ---------------------------------------------------------------------------


async def _run_analyze(settings: Settings, *, resume: bool) -> list[Scene]:
    async with AsyncExitStack() as stack:
        llm = _build_llm(settings)
        video_search = await _build_video_search(settings, stack)
        curator = _build_scene_curator(settings, llm, video_search)
        use_case = AnalyzeScriptUseCase(
            llm,
            curator,
            min_line_length_for_grouping=settings.pipeline.min_line_length_for_grouping,
            max_scenes_before_grouping=settings.pipeline.max_scenes_before_grouping,
            contexto_narrativo=settings.pipeline.contexto_narrativo,
        )
        with _progress_bar("Analizando escenas") as (progress, task_id):

            def _avance(numero: int, total: int) -> None:
                progress.update(task_id, completed=numero, total=total)

            return await use_case.execute(
                settings.script_path,
                settings.scenes_output_path,
                resume=resume,
                on_scene_processed=_avance,
            )


async def _run_download(settings: Settings) -> DownloadSummary:
    async with AsyncExitStack() as stack:
        downloader = await _build_downloader(settings, stack)
        use_case = DownloadClipsUseCase(
            downloader, max_concurrency=settings.pipeline.max_download_concurrency
        )
        with _progress_bar("Descargando clips") as (progress, task_id):

            def _avance(completados: int, total: int) -> None:
                progress.update(task_id, completed=completados, total=total)

            return await use_case.execute(
                settings.scenes_output_path, settings.clips_dir, on_progress=_avance
            )


async def _run_assemble(settings: Settings) -> Path:
    assembler = _build_assembler(settings)
    audio_provider = LocalAudioProvider(
        probe_binary=settings.ffmpeg.probe_binary, timeout=settings.ffmpeg.process_timeout
    )
    use_case = AssembleVideoUseCase(assembler, audio_provider)
    with console.status("Ensamblando video final con FFmpeg..."):
        return await use_case.execute(
            settings.scenes_output_path,
            settings.clips_dir,
            settings.audio_path,
            settings.final_output_path,
        )


async def _run_pipeline(settings: Settings, *, resume: bool) -> PipelineResult:
    async with AsyncExitStack() as stack:
        llm = _build_llm(settings)
        video_search = await _build_video_search(settings, stack)
        curator = _build_scene_curator(settings, llm, video_search)
        analyze_uc = AnalyzeScriptUseCase(
            llm,
            curator,
            min_line_length_for_grouping=settings.pipeline.min_line_length_for_grouping,
            max_scenes_before_grouping=settings.pipeline.max_scenes_before_grouping,
            contexto_narrativo=settings.pipeline.contexto_narrativo,
        )
        downloader = await _build_downloader(settings, stack)
        download_uc = DownloadClipsUseCase(
            downloader, max_concurrency=settings.pipeline.max_download_concurrency
        )
        assembler = _build_assembler(settings)
        audio_provider = LocalAudioProvider(
            probe_binary=settings.ffmpeg.probe_binary, timeout=settings.ffmpeg.process_timeout
        )
        assemble_uc = AssembleVideoUseCase(assembler, audio_provider)

        orchestrator = PipelineOrchestrator(analyze_uc, download_uc, assemble_uc)
        return await orchestrator.run(
            script_path=settings.script_path,
            scenes_output_path=settings.scenes_output_path,
            clips_dir=settings.clips_dir,
            audio_path=settings.audio_path,
            final_output_path=settings.final_output_path,
            resume=resume,
        )


# ---------------------------------------------------------------------------
# Presentación: barras de progreso, spinners y tablas de resumen
# ---------------------------------------------------------------------------


@contextmanager
def _progress_bar(descripcion: str) -> Iterator[tuple[Progress, TaskID]]:
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task_id = progress.add_task(descripcion, total=None)
        yield progress, task_id


def _print_dry_run_plan(settings: Settings, *, escenas_planeadas: int) -> None:
    tabla = Table(title="Plan (dry-run) - no se realizarán llamadas externas")
    tabla.add_column("Parámetro")
    tabla.add_column("Valor")
    tabla.add_row("Guion", str(settings.script_path))
    tabla.add_row("Escenas estimadas", str(escenas_planeadas))
    tabla.add_row("Checkpoint de escenas", str(settings.scenes_output_path))
    tabla.add_row("Carpeta de clips", str(settings.clips_dir))
    tabla.add_row("Audio", str(settings.audio_path))
    tabla.add_row("Video final", str(settings.final_output_path))
    tabla.add_row("Umbral de relevancia", str(settings.pipeline.relevance_threshold))
    tabla.add_row("Concurrencia de descarga", str(settings.pipeline.max_download_concurrency))
    console.print(tabla)


def _print_analyze_summary(escenas: list[Scene]) -> None:
    con_clip = sum(1 for escena in escenas if escena.tiene_clip)
    relevancias = [
        escena.clip_seleccionado.relevancia for escena in escenas if escena.clip_seleccionado
    ]
    promedio = sum(relevancias) / len(relevancias) if relevancias else 0.0

    tabla = Table(title="Resumen del análisis")
    tabla.add_column("Métrica")
    tabla.add_column("Valor", justify="right")
    tabla.add_row("Escenas totales", str(len(escenas)))
    tabla.add_row("Con clip seleccionado", str(con_clip))
    tabla.add_row("Sin clip", str(len(escenas) - con_clip))
    tabla.add_row("Relevancia promedio", f"{promedio:.1f}")
    console.print(tabla)


def _print_download_summary(resumen: DownloadSummary) -> None:
    tabla = Table(title="Resumen de descargas")
    tabla.add_column("Métrica")
    tabla.add_column("Valor", justify="right")
    tabla.add_row("Total escenas", str(resumen.total_escenas))
    tabla.add_row("Descargados", str(resumen.descargados))
    tabla.add_row("Omitidos (ya existían)", str(resumen.omitidos))
    tabla.add_row("Fallidos", str(resumen.fallidos))
    tabla.add_row("Sin clip asignado", str(resumen.sin_clip))
    console.print(tabla)


def _contar_escenas_planeadas(settings: Settings) -> int:
    from lavox.domain.entities.script import Script

    texto = settings.script_path.read_text(encoding="utf-8")
    script = Script.from_text(texto)
    lineas = script.agrupar(
        min_length=settings.pipeline.min_line_length_for_grouping,
        max_scenes_threshold=settings.pipeline.max_scenes_before_grouping,
    )
    return len(lineas)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    guion: Annotated[
        Path | None, typer.Option("--guion", help="Ruta del archivo de guion.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Ruta del checkpoint JSON de escenas.")
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Reanudar desde un checkpoint existente.")
    ] = True,
    max_relevance: Annotated[
        int | None,
        typer.Option("--max-relevance", help="Umbral de relevancia (0-100) para aceptar un clip."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Valida la configuración sin llamar proveedores externos."),
    ] = False,
) -> None:
    """Analiza el guion y cura el mejor clip de video para cada escena."""
    try:
        settings = _load_settings(guion=guion, scenes=output, max_relevance=max_relevance)
        if dry_run:
            if not settings.script_path.exists():
                raise ConfigError(f"No se encuentra el guion: {settings.script_path}")
            _print_dry_run_plan(settings, escenas_planeadas=_contar_escenas_planeadas(settings))
            raise typer.Exit(code=int(ExitCode.OK))

        escenas = asyncio.run(_run_analyze(settings, resume=resume))
    except ConfigError as exc:
        console.print(f"[bold red]Error de configuración:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.CONFIG_ERROR)) from exc
    except LLMError as exc:
        console.print(f"[bold red]Error del proveedor LLM:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.LLM_ERROR)) from exc

    _print_analyze_summary(escenas)
    sin_clip = sum(1 for escena in escenas if not escena.tiene_clip)
    raise typer.Exit(code=int(ExitCode.PARTIAL if sin_clip else ExitCode.OK))


@app.command()
def download(
    scenes: Annotated[
        Path | None, typer.Option("--scenes", help="Checkpoint JSON de escenas a descargar.")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Carpeta donde guardar los clips descargados.")
    ] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", help="Descargas concurrentes máximas.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Valida la configuración sin descargar nada.")
    ] = False,
) -> None:
    """Descarga los clips seleccionados durante el análisis."""
    try:
        settings = _load_settings(scenes=scenes, clips_dir=output, workers=workers)
        if dry_run:
            if not settings.scenes_output_path.exists():
                raise ConfigError(f"No se encontró '{settings.scenes_output_path}'.")
            _print_dry_run_plan(settings, escenas_planeadas=-1)
            raise typer.Exit(code=int(ExitCode.OK))

        resumen = asyncio.run(_run_download(settings))
    except ConfigError as exc:
        console.print(f"[bold red]Error de configuración:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.CONFIG_ERROR)) from exc
    except DownloadError as exc:
        console.print(f"[bold red]Error de descarga:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.DOWNLOAD_ERROR)) from exc

    _print_download_summary(resumen)
    es_parcial = resumen.fallidos > 0 or resumen.sin_clip > 0
    raise typer.Exit(code=int(ExitCode.PARTIAL if es_parcial else ExitCode.OK))


@app.command()
def assemble(
    scenes: Annotated[
        Path | None, typer.Option("--scenes", help="Checkpoint JSON de escenas.")
    ] = None,
    clips_dir: Annotated[
        Path | None, typer.Option("--clips-dir", help="Carpeta con los clips descargados.")
    ] = None,
    audio: Annotated[
        Path | None, typer.Option("--audio", help="Ruta del audio de narración.")
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Ruta del video final.")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Valida la configuración sin ejecutar FFmpeg.")
    ] = False,
) -> None:
    """Ensambla el video final a partir de los clips descargados y el audio."""
    try:
        settings = _load_settings(scenes=scenes, clips_dir=clips_dir, audio=audio, output=output)
        if dry_run:
            for etiqueta, ruta in (
                ("checkpoint de escenas", settings.scenes_output_path),
                ("audio", settings.audio_path),
            ):
                if not ruta.exists():
                    raise ConfigError(f"No se encuentra el {etiqueta}: {ruta}")
            _print_dry_run_plan(settings, escenas_planeadas=-1)
            raise typer.Exit(code=int(ExitCode.OK))

        video_final = asyncio.run(_run_assemble(settings))
    except ConfigError as exc:
        console.print(f"[bold red]Error de configuración:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.CONFIG_ERROR)) from exc
    except AssemblyError as exc:
        console.print(f"[bold red]Error de ensamblaje:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.ASSEMBLY_ERROR)) from exc

    console.print(f"[bold green]Video final listo:[/bold green] {video_final}")
    raise typer.Exit(code=int(ExitCode.OK))


@app.command()
def run(
    guion: Annotated[
        Path | None, typer.Option("--guion", help="Ruta del archivo de guion.")
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Ruta del video final.")] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", help="Descargas concurrentes máximas.")
    ] = None,
    max_relevance: Annotated[
        int | None,
        typer.Option("--max-relevance", help="Umbral de relevancia (0-100) para aceptar un clip."),
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Reanudar desde un checkpoint existente.")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Valida la configuración sin ejecutar el pipeline.")
    ] = False,
) -> None:
    """Ejecuta el pipeline completo: analyze -> download -> assemble."""
    try:
        settings = _load_settings(
            guion=guion, output=output, workers=workers, max_relevance=max_relevance
        )
        if dry_run:
            if not settings.script_path.exists():
                raise ConfigError(f"No se encuentra el guion: {settings.script_path}")
            _print_dry_run_plan(settings, escenas_planeadas=_contar_escenas_planeadas(settings))
            raise typer.Exit(code=int(ExitCode.OK))

        resultado = asyncio.run(_run_pipeline(settings, resume=resume))
    except ConfigError as exc:
        console.print(f"[bold red]Error de configuración:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.CONFIG_ERROR)) from exc
    except LLMError as exc:
        console.print(f"[bold red]Error del proveedor LLM:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.LLM_ERROR)) from exc
    except DownloadError as exc:
        console.print(f"[bold red]Error de descarga:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.DOWNLOAD_ERROR)) from exc
    except AssemblyError as exc:
        console.print(f"[bold red]Error de ensamblaje:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.ASSEMBLY_ERROR)) from exc
    except LavoxError as exc:  # red de seguridad para excepciones de dominio no mapeadas arriba
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=int(ExitCode.CONFIG_ERROR)) from exc

    _print_analyze_summary(resultado.escenas)
    _print_download_summary(resultado.download_summary)
    console.print(f"[bold green]Video final:[/bold green] {resultado.output_video_path}")
    raise typer.Exit(code=int(ExitCode.PARTIAL if resultado.es_parcial else ExitCode.OK))


# ---------------------------------------------------------------------------
# Entry points standalone (lavox-analyze, lavox-download, lavox-assemble)
# ---------------------------------------------------------------------------


def analyze_entrypoint() -> None:
    """Punto de entrada standalone equivalente a ``lavox-pipeline analyze``."""
    typer.run(analyze)


def download_entrypoint() -> None:
    """Punto de entrada standalone equivalente a ``lavox-pipeline download``."""
    typer.run(download)


def assemble_entrypoint() -> None:
    """Punto de entrada standalone equivalente a ``lavox-pipeline assemble``."""
    typer.run(assemble)


if __name__ == "__main__":
    app()
