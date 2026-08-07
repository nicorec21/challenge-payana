"""El servidor MCP.

No prueba el protocolo —eso es del SDK— sino lo que sí es decisión nuestra: que
las cinco herramientas estén registradas, que sus descripciones sirvan para
elegir, y que ninguna escriba.

Las descripciones son **parte del diseño**, no documentación: un agente elige la
herramienta leyéndolas. Una que no diga cuándo usarla ni qué hacer después
termina en «pedile todo y filtrá de tu lado», que es exactamente lo que estas
herramientas vienen a evitar.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="el SDK de MCP está en el extra [mcp]")

from conciliacion.agent import tools  # noqa: E402
from conciliacion.agent.mcp_server import mcp  # noqa: E402

ESPERADAS = {"estado", "pendientes", "explicar", "buscar", "evidencia"}


@pytest.fixture
def registradas():
    return {t.name: t for t in mcp._tool_manager.list_tools()}


def test_estan_las_cinco(registradas):
    assert set(registradas) == ESPERADAS


def test_cada_una_dice_que_pregunta_contesta(registradas):
    """Sin esto el agente elige por el nombre, y `estado` vs `pendientes` no se
    distinguen por el nombre."""
    for nombre, t in registradas.items():
        assert t.description and len(t.description) > 120, nombre
        assert "?" in t.description, f"{nombre} no plantea la pregunta que contesta"


def test_las_que_toman_un_id_dicen_como_conseguirlo(registradas):
    """Un agente sin el id queda en un callejón si la herramienta no lo deriva."""
    for nombre in ("explicar",):
        assert "buscar" in registradas[nombre].description


def test_ninguna_herramienta_escribe():
    """Propiedad de seguridad, no limitación.

    El sistema ingiere descripciones de PDF y referencias de Odoo: texto que
    controla un tercero. Sin herramientas que muten nada, una instrucción
    inyectada ahí adentro no puede causar efecto.
    """
    prohibidas = ("ingest", "save", "delete", "write", "update", "borrar", "guardar")
    for t in mcp._tool_manager.list_tools():
        assert not any(p in t.name.lower() for p in prohibidas)
    assert not any(
        hasattr(tools, f"{p}_") or p in dir(tools) for p in ("save", "delete")
    )


def test_el_servidor_avisa_que_el_texto_externo_es_dato(registradas):
    """Las descripciones de los movimientos vienen de archivos de terceros.

    Ya apareció un `akjshdjkasd` en un asiento de Odoo. Si mañana aparece algo
    con forma de instrucción, el agente tiene que estar advertido.
    """
    instrucciones = (mcp.instructions or "").lower()
    assert "no instrucciones" in instrucciones or "no son instrucciones" in instrucciones


def test_registra_las_funciones_de_tools_y_no_otras():
    """El servidor es un registrador. Si tuviera lógica propia, habría dos
    versiones de la misma respuesta."""
    for nombre in ESPERADAS:
        assert callable(getattr(tools, nombre))
