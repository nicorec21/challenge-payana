"""Connector de la API REST de Wompi.

Dos decisiones que conviene entender antes de tocar esto:

**1. Es un connector específico de Wompi, no un `HttpApiConnector` genérico.**

La tentación es escribir "un connector HTTP configurable". No sirve: cada API
pagina distinto (acá `page`/`page_size` con `meta.total_results`; otras usan
cursores, `Link` headers, o tokens opacos), envuelve distinto y autentica
distinto. Un connector genérico termina siendo un intérprete de configuración
que hay que depurar igual que el código que reemplaza.

La reutilización vive en el **puerto** `Connector`, no en la implementación: el
pipeline, el registry y los adapters no cambian cuando se suma una API nueva.
Eso es lo que hace barato extender. Ver ADR-0002.

**2. La redacción de PII ocurre ANTES de tocar disco.**

`/transactions` devuelve datos de titulares de tarjeta reales: mail, nombre
completo, teléfono, BIN, últimos cuatro dígitos, nombre del tarjetahabiente,
device fingerprint. Un sistema de conciliación contable no tiene por qué
tocar nada de eso, y guardarlo es exposición regulatoria a cambio de nada.

Por eso el connector proyecta sobre una **allowlist** antes de archivar y antes
de emitir. Allowlist y no denylist: si Wompi agrega un campo con PII mañana,
una denylist lo persiste en silencio; la allowlist falla cerrada.
Ver ADR-0006.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import httpx

from ...settings import WompiSettings
from ..ports import FetchWindow, IngestionError, RawRecord

#: Campos de `/transactions` que el sistema puede ver. Todo lo demás se
#: descarta en el borde y nunca toca disco.
#:
#: Los excluidos —`customer_email`, `customer_data`, `payment_method`,
#: `billing_data`, `shipping_address`, `payment_source_id`, `redirect_url`—
#: contienen identidad del comprador o datos de tarjeta. Ninguno participa de
#: la conciliación: para responder "¿esta venta llegó al banco?" alcanzan el
#: id, el monto, la fecha, el estado y el desembolso asociado.
TRANSACTION_FIELDS: frozenset[str] = frozenset({
    "id",
    "created_at",
    "finalized_at",
    "amount_in_cents",
    "currency",
    "reference",
    "payment_method_type",   # "CARD" / "NEQUI" / ... — sin datos de la tarjeta
    "status",
    "status_message",
    "disbursement",          # el eslabón pago → liquidación
    "bill_id",
    "payment_link_id",
})

#: Subcampos permitidos dentro de `disbursement`.
DISBURSEMENT_FIELDS: frozenset[str] = frozenset({
    "id",
    "merchant_id",
    "amount_in_cents",
    "status",
    "created_at",
    "updated_at",
    "bank_account_type",
    # `bank_account_number` queda afuera a propósito: es un dato de cuenta
    # bancaria y la conciliación no lo usa (el extracto provisto es de otra
    # cuenta, ver ADR-0007).
})

#: Tope de páginas. Existe para que un bug de paginación falle rápido y fuerte
#: en vez de girar contra una API productiva.
MAX_PAGES = 200

_PAGE_SIZE = 200  # máximo que acepta la API


def redact_transaction(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Proyecta una transacción sobre la allowlist."""
    out = {k: v for k, v in raw.items() if k in TRANSACTION_FIELDS}
    disbursement = out.get("disbursement")
    if isinstance(disbursement, Mapping):
        out["disbursement"] = {k: v for k, v in disbursement.items() if k in DISBURSEMENT_FIELDS}
    return out


def redact_disbursement(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Proyecta un desembolso. No trae PII, pero sí número de cuenta."""
    return {k: v for k, v in raw.items() if k in DISBURSEMENT_FIELDS}


REDACTORS = {
    "transactions": redact_transaction,
    "disbursements": redact_disbursement,
}


class WompiApiConnector:
    """Trae transacciones o desembolsos de la API de Wompi.

    Emite un `RawRecord` **por ítem**, no por página. A diferencia de un
    extracto en PDF —donde una línea suelta no es interpretable sin el
    encabezado ni la cadena de saldos— un objeto JSON de la API sí se
    interpreta solo. La granularidad del `RawRecord` sigue esa regla, no una
    convención.
    """

    def __init__(
        self,
        resource: str,
        settings: WompiSettings,
        *,
        archive_dir: Path | None = None,
        client: httpx.Client | None = None,
        connector_id: str | None = None,
    ) -> None:
        if resource not in REDACTORS:
            raise ValueError(f"Recurso no soportado: {resource!r}")
        self.resource = resource
        self.settings = settings
        self.archive_dir = archive_dir
        self.connector_id = connector_id or f"wompi_api_{resource}"
        self._client = client
        self._redact = REDACTORS[resource]

    def fetch(self, window: FetchWindow | None = None) -> Iterator[RawRecord]:
        """Recorre las páginas del recurso y emite un registro por ítem.

        La API exige `from_date` y `until_date`: una ventana sin fechas es un
        error de uso, no un pedido de "todo". Fallar acá evita bajar el
        histórico completo de una cuenta productiva por accidente.
        """
        if window is None or window.start is None or window.end is None:
            raise ValueError(
                "La API de Wompi exige un rango de fechas: "
                "pasá FetchWindow(start=..., end=...)"
            )

        client = self._client or httpx.Client(timeout=30.0)
        owns_client = self._client is None
        fetched_at = datetime.now(timezone.utc)

        try:
            for page, items in self._pages(client, window.start, window.end):
                archive = self._archive(window.start, window.end, page, items)
                for index, item in enumerate(items):
                    yield RawRecord(
                        locator=self._locator(archive, item, index),
                        payload=item,
                        fetched_at=fetched_at,
                        metadata={
                            "resource": self.resource,
                            "page": page,
                            "merchant_id": self.settings.merchant_id,
                            "redacted": True,
                        },
                    )
        finally:
            if owns_client:
                client.close()

    # -- interno ---------------------------------------------------------

    def _pages(
        self, client: httpx.Client, start: date, end: date
    ) -> Iterator[tuple[int, list[dict[str, Any]]]]:
        seen, page = 0, 1
        while page <= MAX_PAGES:
            body = self._get(client, start, end, page)
            items = [self._redact(item) for item in body.get("data", [])]
            if not items:
                return

            yield page, items

            meta = body.get("meta") or {}
            total = meta.get("total_results")
            seen += len(items)
            if total is None or seen >= total:
                return
            page += 1

        raise IngestionError(
            f"Se superaron {MAX_PAGES} páginas: la paginación no termina",
            f"{self.resource}:{start}..{end}",
        )

    def _get(
        self, client: httpx.Client, start: date, end: date, page: int
    ) -> dict[str, Any]:
        url = f"{self.settings.api_base_url.rstrip('/')}/{self.resource}"
        params = {
            "from_date": start.isoformat(),
            "until_date": end.isoformat(),
            "page": page,
            "page_size": _PAGE_SIZE,
        }
        locator = f"GET {url} {start}..{end} p{page}"
        try:
            response = client.get(url, params=params, headers=self.settings.auth_header)
        except httpx.HTTPError as exc:
            raise IngestionError(f"Fallo de red: {exc}", locator, exc) from exc

        if response.status_code != 200:
            raise IngestionError(
                f"La API respondió {response.status_code}: {response.text[:200]}", locator
            )
        try:
            return response.json()
        except ValueError as exc:
            raise IngestionError("La respuesta no es JSON", locator, exc) from exc

    def _archive(
        self, start: date, end: date, page: int, items: Sequence[Mapping[str, Any]]
    ) -> Path | None:
        """Persiste la página YA REDACTADA.

        Es la evidencia: `RawRecord.locator` apunta acá, así que una conclusión
        del sistema se puede rastrear hasta un archivo que alguien abre. Que sea
        la versión redactada y no el payload original es deliberado — el sistema
        declara qué campos descarta en el borde, y esa declaración es parte del
        contrato de ingesta, no una pérdida de evidencia.
        """
        if self.archive_dir is None:
            return None
        directory = self.archive_dir / self.resource
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{start:%Y%m%d}-{end:%Y%m%d}-p{page:03d}.json"
        path.write_text(
            json.dumps(list(items), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _locator(self, archive: Path | None, item: Mapping[str, Any], index: int) -> str:
        item_id = item.get("id", index)
        if archive is None:
            return f"wompi_api://{self.resource}#id={item_id}"
        try:
            base = archive.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            base = archive.as_posix()
        return f"{base}#id={item_id}"
