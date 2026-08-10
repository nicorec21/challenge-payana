"""Connector de replay: reemite los payloads que un connector de red archivó.

Los connectors de red (`WompiApiConnector`, `OdooRpcConnector`) persisten cada
página **ya redactada** en `data/raw/` como evidencia (ADR-0006). Ese archivo
alcanza para reproducir la ingesta completa: este connector lo lee y emite los
mismos registros, sin credenciales ni red.

Es lo que hace verdad la promesa de `--offline`: quien clona el repositorio
reconstruye *todos* los ledgers —incluidos los de la API de Wompi y el libro de
Odoo— desde lo versionado, y el CI puede regenerar la conciliación entera y
compararla contra la salida versionada en `docs/salida/`.

El `locator` calca el formato del connector de red (`{archivo}#id={...}`):
un movimiento reingerido por replay cita exactamente la misma evidencia que uno
ingerido en vivo. Como el id de `Movement` es determinista (ADR-0001), los dos
caminos producen ledgers idénticos, y eso es verificable con un diff.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..ports import FetchWindow, IngestionError, RawRecord

#: `lines-p001.json`, `20260101-20260430-p003.json` → número de página.
_PAGE = re.compile(r"-p(\d+)\.json$")


class ArchiveReplayConnector:
    """Lee las páginas JSON archivadas por un connector de red y emite un
    `RawRecord` por ítem, igual que el connector original.

    No escribe nada: el archivo es evidencia y el replay solo la lee.
    """

    def __init__(
        self,
        directory: Path,
        *,
        fragment: str,
        metadata: Mapping[str, Any],
        connector_id: str,
        project_root: Path | None = None,
    ) -> None:
        #: `fragment` es la etiqueta del ancla en el locator: "id" para Wompi
        #: (`...json#id=1234-...`), "line" para Odoo (`...json#line=98`).
        self.directory = Path(directory)
        self.fragment = fragment
        self.metadata = dict(metadata)
        self.connector_id = connector_id
        self._root = project_root or _find_root(self.directory)

    def fetch(self, window: FetchWindow | None = None) -> Iterator[RawRecord]:
        """Emite los ítems archivados, en orden estable por archivo.

        `window` se ignora: el archivo ES la ventana que se le pidió al
        connector de red cuando se capturó. Filtrar acá inventaría una captura
        que nunca ocurrió; el recorte temporal se hace sobre movimientos.
        """
        if not self.directory.is_dir():
            return

        fetched_at = datetime.now(UTC)
        for path in sorted(self.directory.glob("*.json")):
            match = _PAGE.search(path.name)
            page = int(match.group(1)) if match else 1
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise IngestionError(
                    f"Archivo de replay ilegible: {exc}", path.as_posix(), exc
                ) from exc
            if not isinstance(items, list):
                raise IngestionError(
                    "El archivo de replay no es una lista de ítems", path.as_posix()
                )

            base = self._locator_base(path)
            for index, item in enumerate(items):
                yield RawRecord(
                    locator=f"{base}#{self.fragment}={item.get('id', index)}",
                    payload=item,
                    fetched_at=fetched_at,
                    metadata={**self.metadata, "page": page, "replayed": True},
                )

    def _locator_base(self, path: Path) -> str:
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
