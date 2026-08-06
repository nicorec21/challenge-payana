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


#: Direcciones que no son "salir a la red". El event loop de asyncio abre un
#: self-pipe sobre loopback en Windows, y `TestClient` de Starlette lo necesita;
#: bloquear la creación de sockets a secas rompería tests que nunca tocan la red.
_LOCALES = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", ""})


def _es_local(address: object) -> bool:
    if isinstance(address, tuple) and address:
        return str(address[0]) in _LOCALES
    return isinstance(address, (str, bytes))  # sockets unix


@pytest.fixture(autouse=True)
def sin_red(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bloquea las conexiones salientes durante los tests.

    Se intercepta `connect`, no la creación del socket: lo que hay que impedir
    es que un test le pegue a la API productiva de Wompi, no que se abra un
    descriptor. Las pruebas de la API usan `httpx.MockTransport`, que no llega
    a conectar.

    Un test que necesite red de verdad debe marcarse con `@pytest.mark.network`
    y queda excluido de la corrida por defecto.
    """
    if request.node.get_closest_marker("network"):
        return

    def _falla(address: object):
        raise _RedBloqueada(
            f"Este test intentó conectarse a {address!r}. La suite corre sin red "
            "a propósito: usá httpx.MockTransport o un fixture. Si de verdad "
            "necesitás red, marcá el test con @pytest.mark.network."
        )

    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def connect(self, address, *args, **kwargs):  # noqa: ANN001
        if not _es_local(address):
            _falla(address)
        return original_connect(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):  # noqa: ANN001
        if not _es_local(address):
            _falla(address)
        return original_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
