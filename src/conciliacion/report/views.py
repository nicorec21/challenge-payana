"""Construcción de las vistas del contrato a partir de los ledgers.

Estas funciones son proyecciones puras: no consultan fuentes ni deciden nada.
Toda la lógica de negocio vive en el dominio; acá solo se reorganiza para poder
mirarla.
"""

from __future__ import annotations

from collections import defaultdict

from ..domain.ledger import Ledger
from ..domain.money import Money
from ..domain.movement import Movement, MovementKind
from .contract import (
    DisbursementBreakdown,
    MovementView,
    StatementLine,
    StatementView,
    TransactionBreakdown,
    _money,
)


def build_statement(ledger: Ledger, period: str) -> StatementView:
    """Reconstruye un extracto en orden de documento, con la cadena de saldos.

    El orden lo da `metadata["orden"]` que guarda el adapter: el ledger ordena
    por `(fecha, id)` y ese no es el orden del PDF. Ver ADR-0007.
    """
    movimientos = [m for m in ledger if m.metadata.get("periodo") == period]
    movimientos.sort(key=lambda m: m.metadata.get("orden", 0))

    if not movimientos:
        return StatementView(
            ledger_id=ledger.id,
            period=period,
            account_number=None,
            opening_balance=_money(Money.zero(ledger.currency)),
            closing_balance=_money(Money.zero(ledger.currency)),
            line_count=0,
            chain_intact=True,
            lines=[],
        )

    # El saldo de apertura no se guarda como movimiento: se deduce del primero.
    primero = movimientos[0]
    apertura = Money.parse(primero.metadata["saldo"], ledger.currency) - primero.amount

    lines: list[StatementLine] = []
    esperado = apertura
    intacta = True

    for m in movimientos:
        esperado = esperado + m.amount
        declarado = Money.parse(m.metadata["saldo"], ledger.currency)
        ok = esperado == declarado
        intacta = intacta and ok
        lines.append(
            StatementLine(
                position=int(m.metadata.get("orden", 0)),
                movement_id=m.id,
                occurred_on=m.occurred_on.isoformat(),
                description=m.description,
                amount=_money(m.amount),
                running_balance=_money(declarado),
                expected_balance=_money(esperado),
                chain_ok=ok,
                page=m.metadata.get("pagina"),
                raw_ref=m.raw_ref,
            )
        )

    return StatementView(
        ledger_id=ledger.id,
        period=period,
        account_number=primero.metadata.get("cuenta"),
        opening_balance=_money(apertura),
        closing_balance=_money(esperado),
        line_count=len(lines),
        chain_intact=intacta,
        lines=lines,
    )


def statement_periods(ledger: Ledger) -> list[str]:
    return sorted({
        str(p) for m in ledger if (p := m.metadata.get("periodo")) is not None
    })


def _transaction_key(movement: Movement) -> str | None:
    """A qué transacción de canal pertenece un movimiento.

    Cada fuente la nombra distinto: la API usa el id de la transacción como
    `external_id`, el CSV de descuentos la guarda en `metadata`, y el POS la
    codifica en la referencia. Unificar acá evita que las vistas conozcan las
    particularidades de cada adapter.
    """
    if (tx := movement.metadata.get("transaction_id")) is not None:
        return str(tx)
    # Fallback para adapters que codifican la transacción en el `external_id`
    # con un sufijo por componente (`<tx>:comision`). Va antes del caso PAYMENT
    # porque si no el pago quedaría en un grupo propio, separado de sus
    # descuentos, y ninguna transacción cerraría.
    if ":" in movement.external_id:
        return movement.external_id.rsplit(":", 1)[0]
    if movement.kind is MovementKind.PAYMENT:
        return movement.external_id
    return None


def _settlements_by_disbursement(ledger: Ledger) -> dict[str, Movement]:
    return {
        str(m.metadata["disbursement_id"]): m
        for m in ledger
        if m.kind is MovementKind.SETTLEMENT and m.metadata.get("disbursement_id") is not None
    }


def _group_by_transaction(ledger: Ledger) -> dict[str, list[Movement]]:
    """Movimientos agrupados por la transacción a la que pertenecen.

    Los settlements de batch quedan afuera: no son de una transacción sino del
    desembolso que la contiene.
    """
    batch = _settlements_by_disbursement(ledger)
    grupos: dict[str, list[Movement]] = defaultdict(list)
    for m in ledger:
        if m.kind is MovementKind.SETTLEMENT and str(m.metadata.get("disbursement_id")) in batch:
            continue
        if (key := _transaction_key(m)) is not None:
            grupos[key].append(m)
    return grupos


def build_transaction_breakdowns(ledger: Ledger) -> list[TransactionBreakdown]:
    """Descompone cada transacción del canal.

    El cierre en cero solo se afirma cuando el canal liquida **por venta** (el
    POS). Cuando consolida en batch (Wompi), una transacción aislada no puede
    cerrar contra un giro que cubre varias: lo que le corresponde es su
    `net_expected`, y el cierre se verifica en `build_disbursement_breakdowns`.
    """
    batch = _settlements_by_disbursement(ledger)
    salida: list[TransactionBreakdown] = []

    for tx_id, movimientos in _group_by_transaction(ledger).items():
        pago = next((m for m in movimientos if m.kind is MovementKind.PAYMENT), None)
        propio = next((m for m in movimientos if m.kind is MovementKind.SETTLEMENT), None)
        descuentos = [m for m in movimientos if m.kind in (MovementKind.FEE, MovementKind.TAX)]

        disbursement_id = next(
            (m.metadata.get("disbursement_id") for m in movimientos
             if m.metadata.get("disbursement_id") is not None),
            None,
        )
        del_batch = batch.get(str(disbursement_id)) if disbursement_id is not None else None

        if propio is not None:
            scope, liquidacion = "transaction", propio
        elif del_batch is not None:
            scope, liquidacion = "batch", del_batch
        else:
            scope, liquidacion = None, None

        total_descuentos = Money.sum((abs(m.amount) for m in descuentos), ledger.currency)
        bruto = pago.amount if pago else Money.zero(ledger.currency)

        salida.append(
            TransactionBreakdown(
                transaction_id=tx_id,
                ledger_id=ledger.id,
                occurred_on=min(m.occurred_on for m in movimientos).isoformat(),
                status=pago.status.value if pago else "unknown",
                gross=_money(bruto) if pago else None,
                deductions=[MovementView.of(m) for m in descuentos],
                total_deductions=_money(total_descuentos),
                net_expected=_money(bruto - total_descuentos),
                settlement_scope=scope,
                settlement=_money(liquidacion.amount) if liquidacion else None,
                closes_to_zero=(
                    Money.sum((m.amount for m in movimientos), ledger.currency).is_zero
                    if scope == "transaction" else None
                ),
                disbursement_id=disbursement_id,
                has_declared_deductions=bool(descuentos),
                movements=[MovementView.of(m) for m in sorted(movimientos, key=lambda x: x.kind)],
            )
        )

    salida.sort(key=lambda b: (b.occurred_on, b.transaction_id))
    return salida


def build_disbursement_breakdowns(ledger: Ledger) -> list[DisbursementBreakdown]:
    """Agrupa transacciones por desembolso y verifica el cierre.

    El agrupamiento **no es una inferencia**: usa el `disbursement_id` que Wompi
    declara en cada transacción. Por eso la ambigüedad de subconjuntos que
    advierte el enunciado no aplica por esta vía.

    Cuando `Σ net_expected` no llega al giro, la diferencia son descuentos que
    todavía no están declarados (hay CSV de 4 de 56 días). Eso se muestra en
    `residual` en vez de esconderse; inferir esos descuentos es trabajo de la
    Fase 2, no de una vista.
    """
    settlements = _settlements_by_disbursement(ledger)
    por_desembolso: dict[str, list[list[Movement]]] = defaultdict(list)

    for movimientos in _group_by_transaction(ledger).values():
        did = next(
            (m.metadata.get("disbursement_id") for m in movimientos
             if m.metadata.get("disbursement_id") is not None),
            None,
        )
        if did is not None:
            por_desembolso[str(did)].append(movimientos)

    salida: list[DisbursementBreakdown] = []
    for did, settlement in settlements.items():
        grupos = por_desembolso.get(did, [])
        planos = [m for grupo in grupos for m in grupo]

        bruto = Money.sum(
            (m.amount for m in planos if m.kind is MovementKind.PAYMENT), ledger.currency
        )
        descuentos = Money.sum(
            (abs(m.amount) for m in planos if m.kind in (MovementKind.FEE, MovementKind.TAX)),
            ledger.currency,
        )
        neto = bruto - descuentos
        residual = neto - abs(settlement.amount)
        completos = all(
            any(m.kind in (MovementKind.FEE, MovementKind.TAX) for m in grupo)
            for grupo in grupos
        ) and bool(grupos)

        salida.append(
            DisbursementBreakdown(
                disbursement_id=did,
                ledger_id=ledger.id,
                settled_on=settlement.occurred_on.isoformat(),
                settlement=_money(abs(settlement.amount)),
                transaction_count=len(grupos),
                gross_total=_money(bruto),
                declared_deductions=_money(descuentos),
                net_expected=_money(neto),
                residual=_money(residual),
                closes_to_zero=residual.is_zero,
                deductions_complete=completos,
                transaction_ids=sorted(
                    {k for k, v in _group_by_transaction(ledger).items()
                     if any(m.metadata.get("disbursement_id") is not None
                            and str(m.metadata["disbursement_id"]) == did for m in v)}
                ),
            )
        )

    salida.sort(key=lambda d: d.settled_on)
    return salida
