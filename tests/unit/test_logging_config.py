"""Tests de :mod:`lavox.logging_config`."""

from __future__ import annotations

import structlog

from lavox.logging_config import bind_correlation_id, configure_logging


class TestConfigureLogging:
    def test_no_lanza_con_formato_consola(self) -> None:
        configure_logging(log_level="DEBUG", json_format=False)
        assert structlog.is_configured()

    def test_no_lanza_con_formato_json(self) -> None:
        configure_logging(log_level="INFO", json_format=True)
        assert structlog.is_configured()

    def test_nivel_invalido_cae_a_info_por_defecto(self) -> None:
        # No debe lanzar excepción aunque el nivel no exista.
        configure_logging(log_level="NIVEL_QUE_NO_EXISTE", json_format=False)


class TestCorrelationId:
    def test_genera_un_id_si_no_se_provee_uno(self) -> None:
        cid = bind_correlation_id()
        assert isinstance(cid, str)
        assert len(cid) > 0

    def test_usa_el_id_provisto_si_se_da_uno(self) -> None:
        cid = bind_correlation_id("mi-id-fijo")
        assert cid == "mi-id-fijo"
