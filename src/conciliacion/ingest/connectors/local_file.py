"""Connector de archivos locales.

Entrega los bytes de cada archivo que matchea un patrón bajo un directorio. No
interpreta nada: no sabe si es PDF, CSV o XML.

Emite `bytes` y no una ruta a propósito: así el adapter nunca toca el
filesystem y se puede testear con un fixture en memoria. Los parsers son la
parte con más casos borde; que sean funciones puras es lo que los hace
testeables sin montar directorios.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ..ports import FetchWindow, RawRecord


class LocalFileConnector:
    """Lee archivos de un directorio.

    El `locator` de cada registro es la ruta relativa a la raíz del proyecto,
    para que la cadena de explicabilidad apunte a algo que alguien pueda abrir.
    """

    def __init__(
        self,
        directory: Path,
        pattern: str = "*",
        *,
        connector_id: str = "local_file",
        project_root: Path | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.pattern = pattern
        self.connector_id = connector_id
        self._root = project_root or _find_root(self.directory)

    def fetch(self, window: FetchWindow | None = None) -> Iterator[RawRecord]:
        """Emite un registro por archivo, en orden alfabético.

        Orden estable a propósito: la ingesta tiene que ser reproducible, y el
        orden del filesystem no lo es entre sistemas.

        `window` se ignora acá: un archivo local no se puede filtrar por fecha
        sin abrirlo, y abrirlo es trabajo del adapter. El filtrado temporal se
        aplica después, sobre movimientos ya normalizados.
        """
        if not self.directory.is_dir():
            return

        for path in sorted(self.directory.glob(self.pattern)):
            if not path.is_file():
                continue
            yield RawRecord(
                locator=self._locator(path),
                payload=path.read_bytes(),
                fetched_at=datetime.now(UTC),
                metadata={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "suffix": path.suffix.lower(),
                },
            )

    def _locator(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return path.as_posix()


def _find_root(start: Path) -> Path:
    """Raíz del repo, buscando hacia arriba un marcador conocido."""
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start.resolve()
