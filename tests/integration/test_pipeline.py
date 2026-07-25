"""Test de integración de :class:`PipelineOrchestrator`.

Ejercita las tres fases reales (`AnalyzeScriptUseCase`, `DownloadClipsUseCase`,
`AssembleVideoUseCase`) conectadas de verdad entre sí, con dobles de prueba
solo en los bordes de infraestructura (LLM, búsqueda de video, descarga,
ensamblaje, audio). Casos de uso cubiertos: `Orchestrator | pipeline
completo, fallo parcial, resume desde checkpoint`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lavox.application.pipeline.orchestrator import PipelineOrchestrator
from lavox.application.use_cases.analyze_script import AnalyzeScriptUseCase
from lavox.application.use_cases.assemble_video import AssembleVideoUseCase
from lavox.application.use_cases.download_clips import DownloadClipsUseCase
from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import DownloadError
from lavox.domain.ports.video_downloader_port import DownloadResult
from lavox.domain.services.scene_curator import SceneCurator


class RoutingFakeLLM:
    """Doble de `LLMPort`: responde según el contenido del prompt, no por orden.

    Esto lo hace robusto a cambios en el número exacto de llamadas (por
    ejemplo, si `SceneCurator` reintenta), a diferencia de una cola estricta
    de respuestas programadas.
    """

    def __init__(self, *, relevancia_por_narracion: dict[str, int] | None = None) -> None:
        self._relevancia_por_narracion = relevancia_por_narracion or {}
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        raise NotImplementedError

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> Any:
        self.prompts.append(prompt)
        if "director de video" in prompt:
            return {
                "tema_principal": "tema de prueba",
                "emocion_tono": "intriga",
                "tipo_escena": "hook_inicio",
                "elementos_clave": ["a", "b"],
            }
        if "curador experto" in prompt:
            return ["query uno", "query dos", "query tres"]
        if "Evalúa la relevancia" in prompt:
            relevancia = 20  # por defecto, bajo umbral
            for narracion, valor in self._relevancia_por_narracion.items():
                if narracion in prompt:
                    relevancia = valor
                    break
            return [
                {"indice": 0, "relevancia": relevancia, "justificacion": "evaluación de prueba"}
            ]
        if "Extrae 3-5 elementos" in prompt:
            return ["elemento nuevo 1", "elemento nuevo 2"]
        return None


class AlwaysFindsOneClipVideoSearch:
    """Doble de `VideoSearchPort`: siempre devuelve un candidato por query."""

    def __init__(self) -> None:
        self._contador = 0

    async def search(self, query: str, *, per_page: int = 10) -> list[Clip]:
        self._contador += 1
        return [
            Clip(
                id=self._contador,
                url_descarga=f"https://example.com/videos/{self._contador}.mp4",
                query_origen=query,
            )
        ]


class NeverFindsAnyClipVideoSearch:
    """Doble de `VideoSearchPort`: nunca devuelve ningún candidato."""

    async def search(self, query: str, *, per_page: int = 10) -> list[Clip]:
        return []


class FakeVideoDownloader:
    """Doble de `VideoDownloaderPort`: 'descarga' escribiendo bytes falsos a disco."""

    def __init__(self, *, urls_que_fallan: set[str] | None = None) -> None:
        self._urls_que_fallan = urls_que_fallan or set()

    async def download(self, url: str, destino: Path) -> DownloadResult:
        if url in self._urls_que_fallan:
            raise DownloadError(f"fallo simulado para {url}")
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"contenido-falso-de-video")
        return DownloadResult(destino=destino, tamano_bytes=destino.stat().st_size, omitido=False)

    async def download_many(
        self,
        items: Any,
        *,
        max_concurrency: int = 5,
        on_complete: Any = None,
    ) -> list[Any]:
        resultados: list[Any] = []
        for i, (url, destino) in enumerate(items, start=1):
            try:
                resultados.append(await self.download(url, destino))
            except DownloadError as exc:
                resultados.append(exc)
            if on_complete is not None:
                on_complete(i, len(items))
        return resultados


class FakeVideoAssembler:
    """Doble de `VideoAssemblerPort`: 'ensambla' escribiendo un archivo falso."""

    def __init__(self) -> None:
        self.ultima_duracion_por_escena: float | None = None

    async def assemble(
        self,
        scenes: Any,
        *,
        clips_dir: Path,
        audio_path: Path,
        output_path: Path,
        duracion_por_escena: float,
    ) -> Path:
        self.ultima_duracion_por_escena = duracion_por_escena
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video-final-falso")
        return output_path


class FakeAudioProvider:
    """Doble de `AudioProviderPort`: duración fija configurable."""

    def __init__(self, duracion: float = 10.0) -> None:
        self._duracion = duracion

    async def get_duration(self, audio_path: Path) -> float:
        return self._duracion


def _preparar_entradas(tmp_path: Path, narraciones: list[str]) -> tuple[Path, Path]:
    guion = tmp_path / "guion.txt"
    guion.write_text("\n\n".join(narraciones), encoding="utf-8")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio-falso")
    return guion, audio


def _construir_orchestrator(
    llm: RoutingFakeLLM,
    *,
    video_search: Any = None,
    downloader: FakeVideoDownloader | None = None,
    assembler: FakeVideoAssembler | None = None,
    audio_duracion: float = 10.0,
) -> PipelineOrchestrator:
    curador = SceneCurator(
        llm,
        video_search or AlwaysFindsOneClipVideoSearch(),
        relevance_threshold=70,
        max_attempts=1,
    )
    analyze_uc = AnalyzeScriptUseCase(
        llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
    )
    download_uc = DownloadClipsUseCase(downloader or FakeVideoDownloader(), max_concurrency=3)
    assemble_uc = AssembleVideoUseCase(
        assembler or FakeVideoAssembler(), FakeAudioProvider(audio_duracion)
    )
    return PipelineOrchestrator(analyze_uc, download_uc, assemble_uc)


class TestPipelineCompleto:
    async def test_pipeline_completo_sin_degradacion(self, tmp_path: Path) -> None:
        guion, audio = _preparar_entradas(tmp_path, ["Narración uno.", "Narración dos."])
        llm = RoutingFakeLLM(relevancia_por_narracion={"Narración uno.": 95, "Narración dos.": 95})
        orchestrator = _construir_orchestrator(llm)

        resultado = await orchestrator.run(
            script_path=guion,
            scenes_output_path=tmp_path / "escenas.json",
            clips_dir=tmp_path / "videos",
            audio_path=audio,
            final_output_path=tmp_path / "final.mp4",
        )

        assert resultado.es_parcial is False
        assert len(resultado.escenas) == 2
        assert all(escena.tiene_clip for escena in resultado.escenas)
        assert resultado.download_summary.descargados == 2
        assert resultado.download_summary.fallidos == 0
        assert resultado.output_video_path.exists()


class TestFalloParcial:
    async def test_es_parcial_si_alguna_escena_no_consigue_clip(self, tmp_path: Path) -> None:
        guion, audio = _preparar_entradas(tmp_path, ["Única narración sin candidatos."])
        llm = RoutingFakeLLM()
        # Ningún candidato aparece nunca para esta escena: a diferencia de un
        # candidato con relevancia baja (que igual se acepta como mejor
        # esfuerzo), la ausencia total de candidatos sí deja la escena sin
        # clip seleccionado.
        orchestrator = _construir_orchestrator(llm, video_search=NeverFindsAnyClipVideoSearch())

        resultado = await orchestrator.run(
            script_path=guion,
            scenes_output_path=tmp_path / "escenas.json",
            clips_dir=tmp_path / "videos",
            audio_path=audio,
            final_output_path=tmp_path / "final.mp4",
        )

        assert resultado.es_parcial is True
        assert resultado.escenas_sin_clip == 1
        assert resultado.escenas[0].clip_seleccionado is None
        # El video igual se generó, aunque degradado.
        assert resultado.output_video_path.exists()

    async def test_es_parcial_si_alguna_descarga_individual_falla(self, tmp_path: Path) -> None:
        guion, audio = _preparar_entradas(tmp_path, ["Narración uno.", "Narración dos."])
        llm = RoutingFakeLLM(relevancia_por_narracion={"Narración uno.": 95, "Narración dos.": 95})
        # La primera URL de clip generada será "https://example.com/videos/1.mp4".
        downloader = FakeVideoDownloader(urls_que_fallan={"https://example.com/videos/1.mp4"})
        orchestrator = _construir_orchestrator(llm, downloader=downloader)

        resultado = await orchestrator.run(
            script_path=guion,
            scenes_output_path=tmp_path / "escenas.json",
            clips_dir=tmp_path / "videos",
            audio_path=audio,
            final_output_path=tmp_path / "final.mp4",
        )

        assert resultado.es_parcial is True
        assert resultado.download_summary.fallidos == 1
        assert resultado.download_summary.descargados == 1


class TestResumeDesdeCheckpoint:
    async def test_reanuda_el_analisis_desde_un_checkpoint_existente(self, tmp_path: Path) -> None:
        guion, audio = _preparar_entradas(
            tmp_path, ["Narración uno.", "Narración dos.", "Narración tres."]
        )
        checkpoint = tmp_path / "escenas.json"
        escena_previa = Scene(
            numero=1,
            narracion="Narración uno.",
            tema_principal="ya procesada antes",
            clip_seleccionado=Clip(id=1, url_descarga="https://example.com/videos/1.mp4"),
        )
        checkpoint.write_text(json.dumps([escena_previa.to_dict()]), encoding="utf-8")

        llm = RoutingFakeLLM(
            relevancia_por_narracion={
                "Narración dos.": 95,
                "Narración tres.": 95,
            }
        )
        orchestrator = _construir_orchestrator(llm)

        resultado = await orchestrator.run(
            script_path=guion,
            scenes_output_path=checkpoint,
            clips_dir=tmp_path / "videos",
            audio_path=audio,
            final_output_path=tmp_path / "final.mp4",
            resume=True,
        )

        assert len(resultado.escenas) == 3
        assert resultado.escenas[0].tema_principal == "ya procesada antes"
        # A la escena 1 no se le volvió a pedir contexto narrativo (solo 2 y 3).
        prompts_de_contexto = [p for p in llm.prompts if "director de video" in p]
        assert len(prompts_de_contexto) == 2
