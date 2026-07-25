"""Tests de :class:`PexelsSearcher`.

Casos de uso cubiertos: `PexelsSearcher | paginación, filtrado landscape,
selección mejor resolución, rate limit`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from lavox.domain.exceptions import PexelsError
from lavox.infrastructure.video_search.pexels_searcher import PexelsSearcher

_URL = "https://api.pexels.com/videos/search"


@pytest.fixture
async def searcher() -> Any:
    instancia = PexelsSearcher("fake-pexels-key", max_attempts=3, wait_initial=0.001, wait_max=0.01)
    yield instancia
    await instancia.aclose()


class TestBusquedaBasica:
    async def test_mapea_resultados_y_elige_la_mejor_resolucion(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher, pexels_response_json: dict
    ) -> None:
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, json=pexels_response_json))

        clips = await searcher.search("old book")

        assert len(clips) == 2
        primero = clips[0]
        assert primero.id == 1234567
        assert primero.ancho == 1920
        assert primero.alto == 1080
        assert primero.url_descarga.endswith("1234567-hd_1920_1080_25fps.mp4")
        assert primero.tags == ("old book", "vintage", "library")
        assert primero.query_origen == "old book"

    async def test_envia_filtro_landscape_y_ancho_minimo(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher, pexels_response_json: dict
    ) -> None:
        route = respx_mock.get(_URL).mock(
            return_value=httpx.Response(200, json=pexels_response_json)
        )

        await searcher.search("old book")

        peticion = route.calls.last.request
        assert peticion.url.params["orientation"] == "landscape"
        assert peticion.url.params["query"] == "old book"

    async def test_video_sin_video_files_validos_se_descarta(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher
    ) -> None:
        respuesta = {
            "page": 1,
            "per_page": 10,
            "total_results": 1,
            "next_page": None,
            "videos": [
                {
                    "id": 999,
                    "url": "https://pexels.com/x",
                    "duration": 5,
                    "tags": [],
                    "video_files": [],
                }
            ],
        }
        respx_mock.get(_URL).mock(return_value=httpx.Response(200, json=respuesta))

        clips = await searcher.search("algo")

        assert clips == []


class TestRateLimitYErrores:
    async def test_reintenta_en_429_y_luego_tiene_exito(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher, pexels_response_json: dict
    ) -> None:
        route = respx_mock.get(_URL).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json=pexels_response_json),
            ]
        )

        clips = await searcher.search("old book")

        assert len(clips) == 2
        assert route.call_count == 2

    async def test_agota_reintentos_en_429_y_lanza_pexels_error(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher
    ) -> None:
        route = respx_mock.get(_URL).mock(return_value=httpx.Response(429))

        with pytest.raises(PexelsError):
            await searcher.search("old book")

        assert route.call_count == 3  # max_attempts=3

    async def test_error_401_no_se_reintenta(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher
    ) -> None:
        route = respx_mock.get(_URL).mock(return_value=httpx.Response(401))

        with pytest.raises(PexelsError):
            await searcher.search("old book")

        assert route.call_count == 1

    async def test_error_5xx_se_reintenta(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher, pexels_response_json: dict
    ) -> None:
        route = respx_mock.get(_URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=pexels_response_json)]
        )

        clips = await searcher.search("old book")

        assert len(clips) == 2
        assert route.call_count == 2


class TestPaginacion:
    async def test_search_paginated_combina_varias_paginas(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher
    ) -> None:
        pagina_1 = {
            "page": 1,
            "per_page": 1,
            "total_results": 2,
            "next_page": "...",
            "videos": [
                {
                    "id": 1,
                    "url": "https://pexels.com/1",
                    "duration": 5,
                    "tags": ["a"],
                    "video_files": [
                        {"link": "https://videos.pexels.com/1.mp4", "width": 1920, "height": 1080}
                    ],
                }
            ],
        }
        pagina_2 = {
            "page": 2,
            "per_page": 1,
            "total_results": 2,
            "next_page": None,
            "videos": [
                {
                    "id": 2,
                    "url": "https://pexels.com/2",
                    "duration": 5,
                    "tags": ["b"],
                    "video_files": [
                        {"link": "https://videos.pexels.com/2.mp4", "width": 1920, "height": 1080}
                    ],
                }
            ],
        }
        respx_mock.get(_URL, params={"page": "1"}).mock(
            return_value=httpx.Response(200, json=pagina_1)
        )
        respx_mock.get(_URL, params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=pagina_2)
        )

        clips = await searcher.search_paginated("algo", per_page=1, max_pages=2)

        assert [clip.id for clip in clips] == [1, 2]

    async def test_search_paginated_se_detiene_si_una_pagina_esta_vacia(
        self, respx_mock: respx.MockRouter, searcher: PexelsSearcher
    ) -> None:
        vacia = {"page": 1, "per_page": 1, "total_results": 0, "next_page": None, "videos": []}
        route = respx_mock.get(_URL, params={"page": "1"}).mock(
            return_value=httpx.Response(200, json=vacia)
        )

        clips = await searcher.search_paginated("nada", per_page=1, max_pages=3)

        assert clips == []
        assert route.call_count == 1  # no debió pedir la página 2 ni la 3
