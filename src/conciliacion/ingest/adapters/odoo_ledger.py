"""Adapter del libro contable de Odoo.

Proyecta líneas de partida doble al modelo de partida simple.

## La regla, que resultó ser una línea

    monto_con_signo = debit − credit

Sin casos especiales. Las dos cuentas que interesan (`1110001 Wompi Tarjetas` y
`111001 Bank`) son de tipo `asset_cash`, así que **debe = entrada, haber =
salida**: exactamente la convención de signos del modelo. Verificado contra los
datos:

| Modelo | Odoo |
|---|---|
| `PAYMENT +243.698` (wompi) | 1110001 DEBE 243.698 |
| `SETTLEMENT −1.327.369,53` (wompi) | 1110001 HABER 1.327.369,53 |
| `BANK_CREDIT +1.327.369,53` (banco) | 111001 DEBE 1.327.369,53 |

La cuenta `1110001` funciona como **cuenta puente**: la venta la debita, el giro
al banco la acredita. Por eso el giro aparece una sola vez en el ERP y no hay
doble conteo entre libros. Ver ADR-0011.

## Qué NO hace este adapter

**No clasifica.** Todas las líneas salen como `MovementKind.OTHER`. Inferir el
tipo económico del signo sería exactamente la clasificación que los adapters no
hacen (ADR-0007): el signo ya lleva la dirección, y decidir que un débito *es*
una venta es trabajo del motor de conciliación, que además debe explicarlo.

**No filtra los no confirmados.** Un asiento en `draft` o `cancel` se ingiere
con su estado en `metadata`. Descartarlos en ingesta impediría reportar el caso
más interesante: *"el ERP tiene el asiento pero sin confirmar"*, que no es ni
coincidencia ni ausencia.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from ...domain.money import Money
from ...domain.movement import Movement, MovementKind, MovementStatus
from ..ports import IngestionError, RawRecord

#: Estado del asiento en Odoo → estado canónico.
#:
#: `draft` cae en `PENDING` y `cancel` en `VOIDED`, así que ninguno de los dos
#: cuenta para el saldo (`Ledger.balance` solo suma aprobados) pero ambos siguen
#: en el ledger para poder explicarlos.
_STATE = {
    "posted": MovementStatus.APPROVED,
    "draft": MovementStatus.PENDING,
    "cancel": MovementStatus.VOIDED,
}


class OdooLedgerAdapter:
    """Líneas contables de una cuenta → movimientos del libro de un ledger."""

    def __init__(self, ledger_id: str, account_code: str) -> None:
        self.ledger_id = ledger_id
        self.account_code = account_code
        self.source_id = f"odoo_account_{account_code}"

    def sniff(self, record: RawRecord) -> bool:
        payload = record.payload
        return (
            isinstance(payload, Mapping)
            and record.metadata.get("account_code") == self.account_code
            and "debit" in payload
            and "credit" in payload
        )

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        line: Mapping[str, Any] = record.payload
        try:
            amount = _signed(line)
            occurred_on = date.fromisoformat(str(line["date"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise IngestionError(
                f"Línea contable mal formada: {exc}", record.locator, exc
            ) from exc

        yield Movement(
            ledger_id=self.ledger_id,
            source_id=self.source_id,
            #: El id de la línea contable. Estable en Odoo y único: no hace
            #: falta clave sintética como en el extracto en PDF.
            external_id=str(line["id"]),
            occurred_on=occurred_on,
            amount=amount,
            kind=MovementKind.OTHER,
            status=_STATE.get(str(line.get("parent_state", "")), MovementStatus.PENDING),
            description=str(line.get("name") or line.get("ref") or "").strip(),
            reference=str(line["ref"]).strip() if line.get("ref") else None,
            metadata={
                "account_code": self.account_code,
                "debit": str(_dec(line.get("debit"))),
                "credit": str(_dec(line.get("credit"))),
                "move_id": _rel_id(line.get("move_id")),
                "move_name": line.get("move_name") or _rel_name(line.get("move_id")),
                "journal": _rel_name(line.get("journal_id")),
                "state": line.get("parent_state"),
            },
            raw_ref=record.locator,
        )


def _signed(line: Mapping[str, Any]) -> Money:
    """`debit − credit`, en centavos.

    Odoo devuelve floats en `debit`/`credit`. Se convierten vía `Decimal(str(...))`
    y nunca se opera en float: el error de redondeo se volvería indistinguible de
    una diferencia contable real, que es justo lo que hay que detectar.
    """
    return Money.from_units(_dec(line.get("debit")) - _dec(line.get("credit")))


def _dec(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _rel_id(rel: Any) -> Any:
    """Odoo devuelve las relaciones como `[id, nombre]`."""
    return rel[0] if isinstance(rel, (list, tuple)) and rel else None


def _rel_name(rel: Any) -> str | None:
    return str(rel[1]) if isinstance(rel, (list, tuple)) and len(rel) > 1 else None
