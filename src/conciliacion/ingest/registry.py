"""Registro de fuentes y pipeline de ingesta.

Este archivo es la respuesta concreta a "¿cuánto código nuevo hace falta para
sumar el POS?": un `SourceSpec` más, registrado acá. El pipeline no cambia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from ..domain.ledger import Account, Ledger
from ..domain.movement import Movement
from .ports import Adapter, Connector, FetchWindow, IngestionError, RawRecord


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Una fuente = un connector + los adapters que pueden interpretarla.

    Varios adapters por spec es lo que resuelve el caso "el banco cambió el
    layout del PDF en junio": se registran los dos y `sniff` elige.
    """

    name: str
    connector: Connector
    adapters: tuple[Adapter, ...]

    def __post_init__(self) -> None:
        if not self.adapters:
            raise ValueError(f"Fuente '{self.name}' sin adapters")
        ledgers = {a.ledger_id for a in self.adapters}
        if len(ledgers) > 1:
            raise ValueError(
                f"Fuente '{self.name}' tiene adapters de distintos ledgers: {ledgers}"
            )

    @property
    def ledger_id(self) -> str:
        return self.adapters[0].ledger_id


@dataclass
class IngestionReport:
    """Qué pasó durante una ingesta. Se muestra al usuario y se persiste.

    Los `skipped` importan tanto como los `ingested`: un extracto donde el 30%
    de las líneas no parsearon produce una conciliación que parece limpia
    porque le faltan movimientos.
    """

    source_name: str
    ledger_id: str
    records_read: int = 0
    ingested: int = 0
    duplicates: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    adapters_used: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.skipped

    def summary(self) -> str:
        line = (
            f"{self.source_name}: {self.records_read} registros leídos, "
            f"{self.ingested} movimientos nuevos, {self.duplicates} duplicados"
        )
        if self.skipped:
            line += f", {len(self.skipped)} SIN PARSEAR"
        return line


class SourceRegistry:
    """Todas las fuentes conocidas y los ledgers que alimentan."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        self._sources: list[SourceSpec] = []

    def register_account(self, account: Account) -> None:
        self._accounts[account.id] = account

    def register_source(self, spec: SourceSpec) -> None:
        if spec.ledger_id not in self._accounts:
            raise ValueError(
                f"Fuente '{spec.name}' apunta al ledger '{spec.ledger_id}' no registrado"
            )
        self._sources.append(spec)

    @property
    def accounts(self) -> list[Account]:
        return list(self._accounts.values())

    def sources_for(self, ledger_id: str) -> list[SourceSpec]:
        return [s for s in self._sources if s.ledger_id == ledger_id]

    def new_ledger(self, ledger_id: str) -> Ledger:
        """`GenerarLedger(Cuenta)` de las primitivas sugeridas."""
        if ledger_id not in self._accounts:
            raise KeyError(f"Cuenta desconocida: {ledger_id}")
        return Ledger(account=self._accounts[ledger_id])


def ingest(
    spec: SourceSpec,
    ledger: Ledger,
    window: FetchWindow | None = None,
    *,
    strict: bool = False,
) -> IngestionReport:
    """`RegistrarMovimientos(DataSource, Ledger)` de las primitivas sugeridas.

    Por defecto NO es estricto: un registro que no parsea se anota en el reporte
    y la ingesta sigue. Razón: en conciliación es preferible una corrida parcial
    con el faltante señalado que ninguna corrida. `strict=True` para tests y CI.
    """
    report = IngestionReport(source_name=spec.name, ledger_id=ledger.id)

    for record in spec.connector.fetch(window):
        report.records_read += 1
        adapter = _select_adapter(spec, record)

        if adapter is None:
            report.skipped.append((record.locator, "ningún adapter reconoce el formato"))
            if strict:
                raise IngestionError("Ningún adapter reconoce el registro", record.locator)
            continue

        try:
            movements = list(adapter.parse(record))
        except Exception as exc:
            report.skipped.append((record.locator, f"{type(exc).__name__}: {exc}"))
            if strict:
                raise IngestionError("Error de parseo", record.locator, exc) from exc
            continue

        report.adapters_used[adapter.source_id] = (
            report.adapters_used.get(adapter.source_id, 0) + len(movements)
        )
        for movement in movements:
            if ledger.add(movement):
                report.ingested += 1
            else:
                report.duplicates += 1

    return report


def _select_adapter(spec: SourceSpec, record: RawRecord) -> Adapter | None:
    """Primer adapter que reconoce el registro gana.

    El orden de registro es la precedencia: registrar el adapter más específico
    primero. Explícito en vez de un score, porque un empate silencioso entre dos
    parsers es un bug muy difícil de ver en el reporte final.
    """
    for adapter in spec.adapters:
        try:
            if adapter.sniff(record):
                return adapter
        except Exception:
            continue
    return None


def ingest_all(
    registry: SourceRegistry,
    ledger_id: str,
    window: FetchWindow | None = None,
    *,
    strict: bool = False,
) -> tuple[Ledger, list[IngestionReport]]:
    """Construye un ledger completo desde todas sus fuentes."""
    ledger = registry.new_ledger(ledger_id)
    reports = [ingest(spec, ledger, window, strict=strict) for spec in registry.sources_for(ledger_id)]
    return ledger, reports


def iter_movements(spec: SourceSpec, window: FetchWindow | None = None) -> Iterator[Movement]:
    """Atajo para tests: movimientos de una fuente sin tocar un ledger."""
    for record in spec.connector.fetch(window):
        adapter = _select_adapter(spec, record)
        if adapter is not None:
            yield from adapter.parse(record)
