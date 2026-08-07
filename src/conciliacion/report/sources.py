"""Panorama de cada fuente de datos.

Responde tres preguntas por fila, y **ninguna más**:

1. ¿qué trae la fuente?
2. ¿de dónde salió ese dato?
3. ¿está conciliado?

Deliberadamente **no** muestra la conciliación en sí —eso tiene su propia
vista—: acá el estado es un aviso de una palabra. Mezclar las dos cosas
convierte una vista de exploración en un reporte que nadie lee.

Las tres fuentes tienen estructura propia —Wompi agrupa ventas en desembolsos,
el banco es una secuencia de líneas, Odoo son asientos— pero se proyectan a una
**forma común** para que la interfaz sea una sola tabla y no tres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import bank_hints_for
from ..domain.ledger import Ledger
from ..domain.movement import MovementKind
from ..reconcile.erp.findings import ErpReport, ErpStatus
from ..reconcile.flow.findings import FlowReport, FlowStatus
from .contract import _money


class Conciliacion:
    """Estado de conciliación de una fila, en una palabra."""

    CONCILIADO = "conciliado"
    SIN_CONCILIAR = "sin_conciliar"
    #: El dato es ajeno a lo que este sistema concilia. Nómina, impuestos,
    #: servicios: 368 de las 426 líneas del extracto. Marcarlas «sin conciliar»
    #: sería mentir por omisión.
    NO_APLICA = "no_aplica"
    #: Cae fuera del período que cubre alguna de las fuentes necesarias.
    SIN_DATOS = "sin_datos"


@dataclass(frozen=True, slots=True)
class SourceItem:
    """Una fila del panorama, común a las tres fuentes."""

    id: str
    #: Identificador legible: número de desembolso, descripción, nombre de asiento.
    label: str
    #: Contexto en segunda línea: referencia, cuenta contable, diario.
    sublabel: str | None
    date: str | None
    amount: dict[str, Any]
    #: Qué fuentes aportaron este dato. Wompi puede tener dos (API + CSV).
    origins: list[str]
    reconciliation: str
    #: Cuántos elementos contiene, si la fila agrupa (un desembolso agrupa ventas).
    child_count: int | None = None
    #: Datos propios de la fuente que no entran en la forma común.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourcePanorama:
    source_id: str
    ledger_id: str
    name: str
    subtitle: str
    #: Cómo llama esta fuente a sus filas: "desembolsos", "líneas", "asientos".
    unit: str
    total: int
    counts: dict[str, int]
    items: list[SourceItem]


# ── Wompi: ventas agrupadas por desembolso ──────────────────────────────────


def wompi_panorama(
    ledger: Ledger, groups: list[dict], flow: FlowReport | None
) -> SourcePanorama:
    conciliados = _por_estado(ledger, flow, FlowStatus.MATCHED, FlowStatus.AMBIGUOUS)
    fuera = _por_estado(ledger, flow, FlowStatus.OUT_OF_COVERAGE)
    items: list[SourceItem] = []
    for g in groups:
        did = g["disbursement_id"]
        origins = sorted({
            src for t in g["transactions"] for src in _origins_de_transaccion(t)
        })

        if did is None:
            estado = Conciliacion.NO_APLICA  # ventas rechazadas: nunca se liquidan
        elif did in fuera:
            estado = Conciliacion.SIN_DATOS
        elif did in conciliados:
            estado = Conciliacion.CONCILIADO
        else:
            estado = Conciliacion.SIN_CONCILIAR

        items.append(
            SourceItem(
                id=did or "sin-liquidar",
                label=f"Desembolso {did}" if did else "Ventas no liquidadas",
                sublabel=(
                    f"{g['transaction_count']} venta(s) · bruto {g['gross_total']['formatted']}"
                ),
                date=g["settled_on"],
                amount=g["settlement"] or g["gross_total"],
                origins=origins,
                reconciliation=estado,
                child_count=g["transaction_count"],
                extra={
                    "gross_total": g["gross_total"],
                    "declared_deductions": g["declared_deductions"],
                    "deductions_complete": g["deductions_complete"],
                    "transactions": g["transactions"],
                },
            )
        )

    return SourcePanorama(
        source_id="wompi",
        ledger_id=ledger.id,
        name="Wompi",
        subtitle="Canal de cobro · API REST + reportes CSV",
        unit="desembolsos",
        total=len(items),
        counts=_contar(items),
        items=items,
    )


def _origins_de_transaccion(tx: Any) -> set[str]:
    """Qué adapters aportaron los datos de esta venta.

    Wompi puede tener dos: la API trae el bruto y el CSV el desglose fiscal.
    Verlo en la fila es la respuesta a «¿de dónde viene este dato?»."""
    return {m.source_id for m in tx.movements} or {"wompi_api_transactions"}


# ── Bancolombia: líneas del extracto ────────────────────────────────────────


def bancolombia_panorama(ledger: Ledger, flow: FlowReport | None) -> SourcePanorama:
    conciliados = _bank_conciliados(flow)
    hints = bank_hints_for("wompi")

    items: list[SourceItem] = []
    for m in ledger:
        del_canal = m.kind is MovementKind.BANK_CREDIT and any(
            h in m.description.upper() for h in hints
        )
        if m.id in conciliados:
            estado = Conciliacion.CONCILIADO
        elif del_canal:
            estado = Conciliacion.SIN_CONCILIAR
        else:
            estado = Conciliacion.NO_APLICA

        items.append(
            SourceItem(
                id=m.id,
                label=m.description or m.kind.value,
                sublabel=f"cuenta {m.metadata.get('cuenta', '—')} · página {m.metadata.get('pagina', '—')}",
                date=m.occurred_on.isoformat(),
                amount=_money(m.amount),
                origins=[m.source_id],
                reconciliation=estado,
                extra={
                    "saldo": m.metadata.get("saldo"),
                    "periodo": m.metadata.get("periodo"),
                    "raw_ref": m.raw_ref,
                    "kind": m.kind.value,
                },
            )
        )

    return SourcePanorama(
        source_id="bancolombia",
        ledger_id=ledger.id,
        name="Bancolombia",
        subtitle="Cuenta bancaria · extractos PDF",
        unit="líneas",
        total=len(items),
        counts=_contar(items),
        items=items,
    )


# ── Odoo: líneas contables ──────────────────────────────────────────────────


def odoo_panorama(book: Ledger, erp: ErpReport | None) -> SourcePanorama:
    conciliados = _erp_conciliados(erp)

    items: list[SourceItem] = []
    for m in book:
        if m.id in conciliados:
            estado = Conciliacion.CONCILIADO
        elif m.metadata.get("state") != "posted":
            estado = Conciliacion.NO_APLICA  # borrador o anulado: fuera del libro formal
        else:
            estado = Conciliacion.SIN_CONCILIAR

        items.append(
            SourceItem(
                id=m.id,
                label=str(m.metadata.get("move_name") or m.description or "asiento"),
                sublabel=(
                    f"{m.metadata.get('journal', '—')} · cuenta "
                    f"{m.metadata.get('account_code', '—')}"
                    + ("" if m.metadata.get("state") == "posted"
                       else f" · {m.metadata.get('state')}")
                ),
                date=m.occurred_on.isoformat(),
                amount=_money(m.amount),
                origins=[m.source_id],
                reconciliation=estado,
                extra={
                    "debit": m.metadata.get("debit"),
                    "credit": m.metadata.get("credit"),
                    "state": m.metadata.get("state"),
                    "reference": m.reference,
                    "erp_line_id": m.external_id,
                },
            )
        )

    return SourcePanorama(
        source_id="odoo",
        ledger_id=book.id,
        name="Odoo",
        subtitle="Libro contable · XML-RPC",
        unit="líneas contables",
        total=len(items),
        counts=_contar(items),
        items=items,
    )


# ── índices de conciliación ─────────────────────────────────────────────────


def _por_estado(
    ledger: Ledger, flow: FlowReport | None, *estados: FlowStatus
) -> set[str]:
    """Ids de desembolso cuyos giros quedaron en alguno de esos estados.

    El finding apunta al **movimiento** de liquidación; el id del desembolso
    vive en su metadata, así que hay que resolverlo contra el ledger.
    """
    if flow is None:
        return set()
    por_movimiento = {
        m.id: str(m.metadata["disbursement_id"])
        for m in ledger
        if m.kind is MovementKind.SETTLEMENT and m.metadata.get("disbursement_id") is not None
    }
    return {
        did
        for f in flow.of(*estados)
        if (did := por_movimiento.get(f.settlement_movement_id or "")) is not None
    }


def _bank_conciliados(flow: FlowReport | None) -> set[str]:
    if flow is None:
        return set()
    return {
        f.bank_movement_id
        for f in flow.of(FlowStatus.MATCHED, FlowStatus.AMBIGUOUS)
        if f.bank_movement_id
    }


def _erp_conciliados(erp: ErpReport | None) -> set[str]:
    if erp is None:
        return set()
    return {
        f.book_movement_id
        for f in erp.of(ErpStatus.MATCHED, ErpStatus.AMOUNT_MISMATCH)
        if f.book_movement_id
    }


def _contar(items: list[SourceItem]) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(i.reconciliation for i in items))


def paginate(items: list[SourceItem], page: int, size: int) -> tuple[list[SourceItem], int]:
    """Página y cantidad total de páginas.

    La paginación es del servidor y no del navegador: 426 líneas de extracto en
    una sola tabla es un scroll interminable, y cargarlas todas para mostrar 25
    desperdicia lo mismo del lado del cliente.
    """
    total_paginas = max(1, -(-len(items) // size))
    inicio = (max(1, page) - 1) * size
    return items[inicio : inicio + size], total_paginas
