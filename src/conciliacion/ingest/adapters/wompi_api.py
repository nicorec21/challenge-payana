"""Adapters de la API de Wompi: transacciones y desembolsos.

Reparto de responsabilidades entre las tres fuentes de Wompi (ver ADR-0008):

    api_transactions   →  PAYMENT               (bruto, estado, link al batch)
    csv_desembolso     →  FEE + TAX             (desglose fiscal)
    api_disbursements  →  SETTLEMENT            (neto girado)

Cada una emite un conjunto **disjunto** de `MovementKind`. Si dos emitieran el
pago, el bruto se contaría dos veces: tienen distinto `source_id`, así que la
deduplicación por `(source_id, external_id)` no las une —ni debería, porque son
observaciones independientes—.

Con el reparto disjunto, el ledger de Wompi cierra en cero por transacción
liquidada:

    +bruto  −comisión  −impuestos  −neto  =  0

Un saldo distinto de cero significa plata cobrada que todavía no se giró, que
es información contable real y no un artefacto del modelo.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...config import TIMEZONE
from ...domain.money import Money
from ...domain.movement import Movement, MovementKind, MovementStatus
from ..ports import IngestionError, RawRecord

_TZ = ZoneInfo(TIMEZONE)

#: Estados de Wompi → estados canónicos.
_STATUS = {
    "APPROVED": MovementStatus.APPROVED,
    "DECLINED": MovementStatus.DECLINED,
    "ERROR": MovementStatus.ERROR,
    "PENDING": MovementStatus.PENDING,
    "VOIDED": MovementStatus.VOIDED,
}


def _local_date(iso: str) -> datetime:
    """Convierte un timestamp UTC de la API a hora de Colombia.

    Crítico, no cosmético: hay transacciones a las 21:22 COT, que en UTC caen
    al día siguiente. Agrupar por fecha UTC las manda al batch equivocado y el
    día entero deja de conciliar. Verificado contra el epoch embebido en el ID
    de transacción.
    """
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_TZ)


class WompiApiTransactionsAdapter:
    """Transacciones → movimientos `PAYMENT` del ledger de Wompi.

    Ingiere **todos** los estados, no solo los aprobados. Explicar por qué una
    venta no llegó al banco requiere tener esa venta en el ledger; una
    transacción descartada en ingesta es una pregunta que el sistema no puede
    responder. El signo lo pone `Ledger.balance()`, que solo suma aprobados.
    """

    source_id = "wompi_api_transactions"
    ledger_id = "wompi"

    def sniff(self, record: RawRecord) -> bool:
        payload = record.payload
        return (
            isinstance(payload, Mapping)
            and record.metadata.get("resource") == "transactions"
            and "amount_in_cents" in payload
            and isinstance(payload.get("id"), str)
        )

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        tx: Mapping[str, Any] = record.payload
        try:
            local = _local_date(tx["created_at"])
            amount = Money(int(tx["amount_in_cents"]), tx.get("currency", "COP"))
        except (KeyError, ValueError, TypeError) as exc:
            raise IngestionError(f"Transacción mal formada: {exc}", record.locator, exc) from exc

        disbursement = tx.get("disbursement") or {}
        disbursement_id = disbursement.get("id") if isinstance(disbursement, Mapping) else None

        yield Movement(
            ledger_id=self.ledger_id,
            source_id=self.source_id,
            external_id=str(tx["id"]),
            occurred_on=local.date(),
            occurred_at=local,
            amount=amount,
            kind=MovementKind.PAYMENT,
            status=_STATUS.get(str(tx.get("status", "")).upper(), MovementStatus.PENDING),
            description=f"Pago Wompi {tx.get('payment_method_type', '?')}",
            reference=tx.get("reference"),
            metadata={
                # El eslabón pago → liquidación, declarado por Wompi. Con esto,
                # agrupar pagos en su settlement es un GROUP BY y no una
                # búsqueda de subconjuntos: la ambigüedad que advierte el
                # enunciado no aplica por esta vía.
                "disbursement_id": disbursement_id,
                "payment_method_type": tx.get("payment_method_type"),
                "status_message": tx.get("status_message"),
                "finalized_at": tx.get("finalized_at"),
            },
            raw_ref=record.locator,
        )


class WompiApiDisbursementsAdapter:
    """Desembolsos → movimientos `SETTLEMENT`.

    Signo negativo: desde la cuenta de Wompi, el giro al banco es una salida.
    El mismo hecho aparece positivo en el ledger de Bancolombia, y relacionarlos
    es justo lo que hace la Fase 2.
    """

    source_id = "wompi_api_disbursements"
    ledger_id = "wompi"

    def sniff(self, record: RawRecord) -> bool:
        payload = record.payload
        return (
            isinstance(payload, Mapping)
            and record.metadata.get("resource") == "disbursements"
            and "amount_in_cents" in payload
            and isinstance(payload.get("id"), int)
        )

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        d: Mapping[str, Any] = record.payload
        try:
            local = _local_date(d["created_at"])
            net = Money(int(d["amount_in_cents"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise IngestionError(f"Desembolso mal formado: {exc}", record.locator, exc) from exc

        yield Movement(
            ledger_id=self.ledger_id,
            source_id=self.source_id,
            external_id=str(d["id"]),
            occurred_on=local.date(),
            occurred_at=local,
            amount=-net,
            kind=MovementKind.SETTLEMENT,
            status=_STATUS.get(str(d.get("status", "")).upper(), MovementStatus.PENDING),
            description="Liquidación Wompi al banco",
            reference=str(d["id"]),
            metadata={
                "disbursement_id": d["id"],
                "updated_at": d.get("updated_at"),
                "bank_account_type": d.get("bank_account_type"),
            },
            raw_ref=record.locator,
        )
