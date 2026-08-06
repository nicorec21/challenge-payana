"""Configuración común de la suite.

Dos garantías que se hacen cumplir acá en vez de confiar en la disciplina:

1. **Ningún test abre una conexión de red.** Si alguien escribe un test que le
   pega a la API productiva de Wompi, falla con un mensaje explícito en vez de
   pasar en la máquina del autor y romper en CI —o peor: pasar en CI y consumir
   cuota de una cuenta de producción compartida—.

2. **Marcado automático `unit` / `integration`.** Evita tener que acordarse de
   poner el marker en cada archivo, que es exactamente el tipo de cosa que se
   olvida.
"""

from __future__ import annotations

import socket

import pytest

#: Suites que ejercitan solo el dominio: sin filesystem, sin base, sin parsers.
#: Corren en milisegundos y son las que se rompen primero si el modelo cambia.
UNIT_MODULES = frozenset({
    "test_money",
    "test_ledger",
    "test_calendar",
    "test_config_fees",
})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Marca cada test según el módulo al que pertenece."""
    for item in items:
        module = item.path.stem
        marker = "unit" if module in UNIT_MODULES else "integration"
        item.add_marker(getattr(pytest.mark, marker))


class _RedBloqueada(RuntimeError):
    """Un test intentó salir a la red."""


@pytest.fixture(autouse=True)
def sin_red(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bloquea la creación de sockets durante los tests.

    Las pruebas de la API de Wompi usan `httpx.MockTransport`, que no abre
    sockets. Un test que necesite red de verdad debe marcarse explícitamente
    con `@pytest.mark.network` y queda excluido de la corrida por defecto.
    """
    if request.node.get_closest_marker("network"):
        return

    def bloqueado(*args: object, **kwargs: object):
        raise _RedBloqueada(
            "Este test intentó abrir una conexión de red. La suite corre sin red "
            "a propósito: usá httpx.MockTransport o un fixture. Si de verdad "
            "necesitás red, marcá el test con @pytest.mark.network."
        )

    monkeypatch.setattr(socket, "socket", bloqueado)
    monkeypatch.setattr(socket, "create_connection", bloqueado)
