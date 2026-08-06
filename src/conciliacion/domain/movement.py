"""Movimiento canónico: la unidad mínima de verdad financiera.

Un `Movement` es un hecho inmutable observado en una cuenta. No se edita: si la
fuente corrige un dato, se ingiere un movimiento nuevo. Toda la conciliación se
construye sobre movimientos, nunca sobre los payloads crudos.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from .money import Money


class MovementKind(StrEnum):
    """Qué representa económicamente el movimiento.

    Deliberadamente chico. `kind` no dice de qué fuente vino (eso es
    `source_id`) ni cómo se contabiliza (eso es del ERP): dice qué le pasó a la
    plata. Agregar un canal nuevo no debería requerir agregar kinds.
    """

    #: Cobro a un cliente final. En el ledger del canal, positivo.
    PAYMENT = "payment"
    #: Devolución total o parcial de un cobro previo.
    REFUND = "refund"
    #: Contracargo iniciado por el cliente o el emisor.
    CHARGEBACK = "chargeback"
    #: Comisión del intermediario. Negativo.
    FEE = "fee"
    #: Impuesto sobre la comisión (IVA) o retención. Negativo.
    TAX = "tax"
    #: Giro del canal hacia el banco. Negativo en el canal, positivo en el banco.
    SETTLEMENT = "settlement"
    #: Crédito o débito en la cuenta bancaria que todavía no clasificamos.
    BANK_CREDIT = "bank_credit"
    BANK_DEBIT = "bank_debit"
    #: Cualquier otra cosa. Se ingiere igual: preferimos un movimiento sin
    #: clasificar a un movimiento perdido.
    OTHER = "other"


class MovementStatus(StrEnum):
    """Estado en la fuente de origen.

    Solo los `APPROVED` participan de la conciliación de flujo; el resto se
    ingiere igual porque explicar por qué un pago *no* llegó al banco requiere
    tenerlo en el ledger.
    """

    APPROVED = "approved"
    PENDING = "pending"
    DECLINED = "declined"
    VOIDED = "voided"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Movement:
    """Un movimiento de dinero en una cuenta.

    Partida simple: `amount` lleva el signo (ingresos +, egresos -) y pertenece
    a exactamente un ledger.
    """

    #: Ledger al que pertenece. Ej: "wompi", "bancolombia".
    ledger_id: str
    #: Fuente que lo produjo. Ej: "wompi_api", "bancolombia_pdf_layout_a".
    source_id: str
    #: Identificador del movimiento *en la fuente*. Junto con `source_id` forma
    #: la clave de idempotencia: reingerir el mismo archivo no duplica.
    external_id: str
    #: Fecha contable del movimiento (la que usa el matcher para las ventanas).
    occurred_on: date
    amount: Money
    kind: MovementKind
    status: MovementStatus = MovementStatus.APPROVED
    #: Momento exacto, si la fuente lo da. El extracto bancario suele no darlo.
    occurred_at: datetime | None = None
    description: str = ""
    #: Referencia declarada por la fuente (nro de comprobante, referencia de
    #: pago). Es la señal más fuerte para matchear cuando existe.
    reference: str | None = None
    counterparty: str | None = None
    #: Metadata específica de la fuente que no entra en el modelo canónico.
    #: Se preserva para poder explicar; no se usa como llave.
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: Puntero al payload crudo del que salió (`data/raw/...#linea`).
    raw_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.ledger_id:
            raise ValueError("Movement.ledger_id es obligatorio")
        if not self.source_id:
            raise ValueError("Movement.source_id es obligatorio")
        if not self.external_id:
            raise ValueError("Movement.external_id es obligatorio")

    @property
    def id(self) -> str:
        """ID canónico, determinista y estable entre corridas.

        Determinista a propósito: dos corridas sobre la misma data producen los
        mismos IDs, así los reportes son diffeables y las explicaciones citables.
        """
        digest = hashlib.sha256(f"{self.source_id}\x1f{self.external_id}".encode()).hexdigest()
        return f"mov_{digest[:16]}"

    @property
    def idempotency_key(self) -> tuple[str, str]:
        return (self.source_id, self.external_id)

    @property
    def is_inflow(self) -> bool:
        return self.amount.amount > 0

    @property
    def is_outflow(self) -> bool:
        return self.amount.amount < 0

    @property
    def counts_for_reconciliation(self) -> bool:
        return self.status is MovementStatus.APPROVED

    def with_metadata(self, **extra: Any) -> Movement:
        from dataclasses import replace

        return replace(self, metadata={**self.metadata, **extra})
