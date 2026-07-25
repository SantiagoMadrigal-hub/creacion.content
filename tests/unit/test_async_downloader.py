"""Tests de :class:`AsyncDownloader`.

Casos de uso cubiertos: `AsyncDownloader | concurrencia controlada, resume,
validación tamaño, cleanup en error`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from lavox.domain.exceptions import DownloadError
from lavox.infrastructure.download.async_downloader import AsyncDownloader

_URL = "https://videos.pexels.com/video-files/1/1.mp4"
_CONTENIDO_VALIDO = b"x" * 2048  # > 1024 bytes: pasa la validación de tamaño mínimo
_CONTENIDO_CORRUPTO = b"x" * 100  # < 1024 bytes: no pasa la validación


@pytest.fixture
async def downloader() -> Any:
    instancia = AsyncDownloader(max_attempts=3, wait_initial=0.001, wait_max=0.01)
    yield instancia
    await instancia.aclose()


class TestDescargaIndividual:
    async def test_descarga_exitosa_escribe_el_archivo(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, content=_CONTENIDO_VALIDO))
        destino = tmp_path / "escena_1.mp4"

        resultado = await downloader.download(_URL, destino)

        assert resultado.omitido is False
        assert resultado.tamano_bytes == len(_CONTENIDO_VALIDO)
        assert destino.read_bytes() == _CONTENIDO_VALIDO

    async def test_reanuda_si_el_archivo_ya_existe_y_es_valido(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        route = respx_mock.get(_URL).mock(
            return_value=httpx.Response(200, content=_CONTENIDO_VALIDO)
        )
        destino = tmp_path / "escena_1.mp4"
        destino.write_bytes(_CONTENIDO_VALIDO)

        resultado = await downloader.download(_URL, destino)

        assert resultado.omitido is True
        assert not route.called  # no debió hacer ninguna petición HTTP

    async def test_reintenta_descarga_si_el_archivo_existente_es_muy_pequeno(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, content=_CONTENIDO_VALIDO))
        destino = tmp_path / "escena_1.mp4"
        destino.write_bytes(b"muy chico")

        resultado = await downloader.download(_URL, destino)

        assert resultado.omitido is False
        assert destino.read_bytes() == _CONTENIDO_VALIDO

    async def test_archivo_descargado_muy_pequeno_lanza_error_y_se_limpia(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, content=_CONTENIDO_CORRUPTO))
        destino = tmp_path / "escena_1.mp4"

        with pytest.raises(DownloadError):
            await downloader.download(_URL, destino)

        assert not destino.exists()

    async def test_error_http_no_recuperable_limpia_el_archivo_parcial(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(404))
        destino = tmp_path / "escena_1.mp4"

        with pytest.raises(DownloadError):
            await downloader.download(_URL, destino)

        assert not destino.exists()

    async def test_reintenta_en_error_5xx_y_luego_tiene_exito(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        route = respx_mock.get(_URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, content=_CONTENIDO_VALIDO)]
        )
        destino = tmp_path / "escena_1.mp4"

        resultado = await downloader.download(_URL, destino)

        assert resultado.tamano_bytes == len(_CONTENIDO_VALIDO)
        assert route.call_count == 2


class TestDescargaConcurrente:
    async def test_download_many_respeta_max_concurrency(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        contador_activo = 0
        pico_maximo = 0
        lock = asyncio.Lock()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal contador_activo, pico_maximo
            async with lock:
                contador_activo += 1
                pico_maximo = max(pico_maximo, contador_activo)
            await asyncio.sleep(0.03)
            async with lock:
                contador_activo -= 1
            return httpx.Response(200, content=_CONTENIDO_VALIDO)

        respx_mock.get(url__regex=r"https://videos\.pexels\.com/.*").mock(side_effect=handler)
        items = [
            (f"https://videos.pexels.com/{i}.mp4", tmp_path / f"escena_{i}.mp4") for i in range(6)
        ]

        resultados = await downloader.download_many(items, max_concurrency=2)

        assert all(not isinstance(r, BaseException) for r in resultados)
        assert pico_maximo <= 2
        assert pico_maximo >= 2  # confirma paralelismo real, no puramente secuencial

    async def test_un_fallo_no_cancela_las_demas_descargas(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get("https://videos.pexels.com/ok1.mp4").mock(
            return_value=httpx.Response(200, content=_CONTENIDO_VALIDO)
        )
        respx_mock.get("https://videos.pexels.com/falla.mp4").mock(return_value=httpx.Response(404))
        respx_mock.get("https://videos.pexels.com/ok2.mp4").mock(
            return_value=httpx.Response(200, content=_CONTENIDO_VALIDO)
        )
        items = [
            ("https://videos.pexels.com/ok1.mp4", tmp_path / "ok1.mp4"),
            ("https://videos.pexels.com/falla.mp4", tmp_path / "falla.mp4"),
            ("https://videos.pexels.com/ok2.mp4", tmp_path / "ok2.mp4"),
        ]

        resultados = await downloader.download_many(items, max_concurrency=3)

        assert not isinstance(resultados[0], BaseException)
        assert isinstance(resultados[1], DownloadError)
        assert not isinstance(resultados[2], BaseException)

    async def test_on_complete_se_invoca_por_cada_item_terminado(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(url__regex=r"https://videos\.pexels\.com/.*").mock(
            return_value=httpx.Response(200, content=_CONTENIDO_VALIDO)
        )
        items = [
            (f"https://videos.pexels.com/{i}.mp4", tmp_path / f"escena_{i}.mp4") for i in range(4)
        ]
        avances: list[tuple[int, int]] = []

        await downloader.download_many(
            items, max_concurrency=2, on_complete=lambda c, t: avances.append((c, t))
        )

        assert len(avances) == 4
        assert avances[-1] == (4, 4)
        # Los "completados" deben ser una secuencia creciente de 1 a 4.
        assert sorted(a[0] for a in avances) == [1, 2, 3, 4]

    async def test_on_complete_roto_no_corrompe_el_resultado_de_la_descarga(
        self, respx_mock: respx.MockRouter, downloader: AsyncDownloader, tmp_path: Path
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, content=_CONTENIDO_VALIDO))
        destino = tmp_path / "escena_1.mp4"

        def callback_roto(completados: int, total: int) -> None:
            raise RuntimeError("bug en el código del llamador, no en el downloader")

        resultados = await downloader.download_many([(_URL, destino)], on_complete=callback_roto)

        assert len(resultados) == 1
        assert not isinstance(resultados[0], BaseException)
        assert resultados[0].tamano_bytes == len(_CONTENIDO_VALIDO)
