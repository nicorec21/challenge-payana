"""Connector XML-RPC de Odoo. Solo lectura.

Trae las líneas contables (`account.move.line`) de una cuenta del plan.

**El libro de un ledger se define por CUENTA, no por diario.** Verificado contra
los datos: las líneas de `1110001 Wompi Tarjetas` aparecen en tres diarios
distintos —Wompi Tarjetas (40), Bancolombia (11) y Miscellaneous (2)—. Tomar el
diario como libro perdería 13 líneas, incluidos los 11 giros al banco. Ver
ADR-0011.

Igual que con Wompi, se proyecta sobre una **allowlist** antes de archivar: el
ERP tiene datos de terceros (`partner_id`, direcciones, notas) que no participan
de la conciliación. Ver ADR-0006.
"""

from __future__ import annotations

import json
import xmlrpc.client
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...settings import OdooSettings
from ..ports import FetchWindow, IngestionError, RawRecord

#: Campos de `account.move.line` que el sistema puede ver.
#:
#: Quedan afuera `partner_id`, `narration`, direcciones y notas: identifican
#: terceros y no hacen falta para responder si el ERP refleja lo que pasó.
LINE_FIELDS: tuple[str, ...] = (
    "id",
    "date",
    "ref",
    "name",
    "debit",
    "credit",
    "account_id",
    "journal_id",
    "move_id",
    "move_name",
    #: `posted` | `draft` | `cancel`. Decisivo: un asiento anulado o en borrador
    #: no es parte del libro formal.
    "parent_state",
)

#: Tope de páginas. Que falle rápido si la paginación no termina.
MAX_PAGES = 200
_PAGE_SIZE = 500


class OdooRpcConnector:
    """Lee líneas contables de una cuenta.

    No escribe nada en Odoo: solo `search_read`. El ERP es el system of record
    de la empresa; este sistema lo audita, no lo modifica.
    """

    def __init__(
        self,
        account_code: str,
        settings: OdooSettings,
        *,
        archive_dir: Path | None = None,
        connector_id: str | None = None,
        proxy_factory: Any | None = None,
    ) -> None:
        self.account_code = account_code
        self.settings = settings
        self.archive_dir = archive_dir
        self.connector_id = connector_id or f"odoo_rpc_{account_code}"
        #: Inyectable para testear sin red.
        self._proxy_factory = proxy_factory or self._default_proxy

    def _default_proxy(self, endpoint: str):  # pragma: no cover - requiere red
        return xmlrpc.client.ServerProxy(
            f"{self.settings.url.rstrip('/')}/xmlrpc/2/{endpoint}"
        )

    def fetch(self, window: FetchWindow | None = None) -> Iterator[RawRecord]:
        models = self._proxy_factory("object")
        account_id = self._resolve_account(models)
        fetched_at = datetime.now(UTC)

        for page, lines in enumerate(self._pages(models, account_id, window), start=1):
            archive = self._archive(page, lines)
            for line in lines:
                yield RawRecord(
                    locator=self._locator(archive, line),
                    payload=line,
                    fetched_at=fetched_at,
                    metadata={
                        "account_code": self.account_code,
                        "page": page,
                        "redacted": True,
                    },
                )

    # -- interno ---------------------------------------------------------

    def _call(self, models: Any, model: str, method: str, *args: Any, **kw: Any) -> Any:
        try:
            return models.execute_kw(
                self.settings.db, self.settings.user_id, self.settings.api_key,
                model, method, list(args), kw,
            )
        except Exception as exc:
            raise IngestionError(
                f"Odoo rechazó {model}.{method}: {exc}",
                f"odoo://{self.account_code}",
                exc,
            ) from exc

    def _resolve_account(self, models: Any) -> int:
        found = self._call(
            models, "account.account", "search_read",
            [["code", "=", self.account_code]], fields=["id", "code", "name"],
        )
        if not found:
            raise IngestionError(
                f"La cuenta {self.account_code} no existe en el plan de cuentas",
                f"odoo://{self.account_code}",
            )
        return int(found[0]["id"])

    def _pages(
        self, models: Any, account_id: int, window: FetchWindow | None
    ) -> Iterator[list[dict[str, Any]]]:
        domain: list[Any] = [["account_id", "=", account_id]]
        if window and window.start:
            domain.append(["date", ">=", window.start.isoformat()])
        if window and window.end:
            domain.append(["date", "<=", window.end.isoformat()])

        for page in range(MAX_PAGES):
            lines = self._call(
                models, "account.move.line", "search_read", domain,
                fields=list(LINE_FIELDS), limit=_PAGE_SIZE,
                offset=page * _PAGE_SIZE, order="date, id",
            )
            if not lines:
                return
            yield [self._redact(x) for x in lines]
            if len(lines) < _PAGE_SIZE:
                return

        raise IngestionError(
            f"Se superaron {MAX_PAGES} páginas", f"odoo://{self.account_code}"
        )

    def _redact(self, line: dict[str, Any]) -> dict[str, Any]:
        """Allowlist. Falla cerrada: un campo nuevo de Odoo no entra solo."""
        return {k: v for k, v in line.items() if k in LINE_FIELDS}

    def _archive(self, page: int, lines: list[dict[str, Any]]) -> Path | None:
        if self.archive_dir is None:
            return None
        directory = self.archive_dir / self.account_code
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"lines-p{page:03d}.json"
        path.write_text(
            json.dumps(lines, indent=2, ensure_ascii=False, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return path

    def _locator(self, archive: Path | None, line: dict[str, Any]) -> str:
        if archive is None:
            return f"odoo://{self.account_code}#line={line.get('id')}"
        try:
            base = archive.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            base = archive.as_posix()
        return f"{base}#line={line.get('id')}"
