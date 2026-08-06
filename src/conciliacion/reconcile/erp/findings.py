"""Resultados de la conciliación contra el libro del ERP.

El enunciado pide, línea por línea:

> **Coincidencia:** mostrar el dato con el ID del Ledger y el ID del
> LibroContable, dejando claro que representan la misma cosa.
> **Discrepancia:** si un dato está en una fuente pero no en la otra, indicarlo
> explícitamente (y, en lo posible, por qué).

De ahí salen los estados. La parte del *"por qué"* vive en la `Explanation` de
cada finding, igual que en la conciliación de flujo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from ...domain.explanation import Confidence, Explanation
from ...domain.money import Money


class ErpStatus(StrEnum):
    """Qué concluyó el motor sobre una línea."""

    #: El movimiento y la línea contable representan la misma cosa.
    MATCHED = "matched"

    #: Se emparejaron por referencia pero los montos difieren. Es el caso más
    #: grave: el ERP registró el hecho con otro número.
    AMOUNT_MISMATCH = "amount_mismatch"

    #: Pasó en la realidad y el ERP no lo registró.
    MISSING_IN_ERP = "missing_in_erp"

    #: El ERP lo registra y no hay movimiento que lo respalde.
    MISSING_IN_LEDGER = "missing_in_ledger"

    #: El asiento existe pero está en borrador o anulado, así que no forma parte
    #: del libro formal. Ni coincidencia ni ausencia: un estado propio.
    NOT_POSTED = "not_posted"

    #: Cae fuera del período que cubre alguna de las dos fuentes.
    OUT_OF_COVERAGE = "out_of_coverage"

    @property
    def is_problem(self) -> bool:
        return self in (
            ErpStatus.AMOUNT_MISMATCH,
            ErpStatus.MISSING_IN_ERP,
            ErpStatus.MISSING_IN_LEDGER,
            ErpStatus.NOT_POSTED,
        )


@dataclass(frozen=True, slots=True)
class ErpFinding:
    """Una comparación línea a línea, con su explicación.

    Cuando hay coincidencia lleva **los dos identificadores**, que es lo que el
    enunciado pide explícitamente: el del movimiento del ledger y el de la línea
    del libro contable.
    """

    status: ErpStatus
    explanation: Explanation

    #: Id del movimiento en el ledger operativo.
    ledger_movement_id: str | None = None
    #: Id del movimiento que espeja la línea contable del ERP.
    book_movement_id: str | None = None
    #: Id de la línea en Odoo (`account.move.line.id`), para poder abrirla ahí.
    erp_line_id: str | None = None
    #: Nombre del asiento (`WMP/2026/00001`), que es como lo ve un contador.
    erp_move_name: str | None = None

    occurred_on: date | None = None
    ledger_amount: Money | None = None
    book_amount: Money | None = None
    #: Qué tipo de movimiento es del lado del ledger. Sirve para agrupar el
    #: reporte: "el ERP no registra ninguna comisión" es una sola conclusión,
    #: no 27 líneas sueltas.
    kind: str | None = None

    @property
    def confidence(self) -> Confidence:
        return self.explanation.confidence

    @property
    def difference(self) -> Money | None:
        if self.ledger_amount is None or self.book_amount is None:
            return None
        return self.book_amount - self.ledger_amount


@dataclass
class ErpReport:
    """Resultado de conciliar un ledger contra su libro contable."""

    ledger_id: str
    book_ledger_id: str
    account_code: str
    findings: list[ErpFinding] = field(default_factory=list)

    def of(self, *statuses: ErpStatus) -> list[ErpFinding]:
        wanted = set(statuses)
        return [f for f in self.findings if f.status in wanted]

    @property
    def problems(self) -> list[ErpFinding]:
        return [f for f in self.findings if f.status.is_problem]

    def counts(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(f.status.value for f in self.findings))

    def by_kind(self, status: ErpStatus) -> dict[str, dict[str, object]]:
        """Agrupa un estado por tipo de movimiento.

        *"El ERP no registra comisiones"* es una conclusión; 27 findings de
        comisión suelta son ruido con la misma información.
        """
        from collections import defaultdict

        grupos: dict[str, list[ErpFinding]] = defaultdict(list)
        for f in self.of(status):
            grupos[f.kind or "desconocido"].append(f)
        return {
            kind: {
                "count": len(items),
                "total": Money.sum(
                    f.ledger_amount or f.book_amount for f in items
                     if f.ledger_amount or f.book_amount
                ),
            }
            for kind, items in sorted(grupos.items())
        }

    def matched_amount(self, currency: str = "COP") -> Money:
        return Money.sum(
            (f.ledger_amount for f in self.of(ErpStatus.MATCHED) if f.ledger_amount),
            currency,
        )

    def coverage_ratio(self) -> float:
        """Qué fracción de los movimientos del ledger están en el libro.

        Es el número que responde *"¿mi ERP refleja lo que pasó?"* de un vistazo.
        """
        del_ledger = [f for f in self.findings if f.ledger_movement_id]
        if not del_ledger:
            return 0.0
        conciliados = sum(1 for f in del_ledger if f.status is ErpStatus.MATCHED)
        return conciliados / len(del_ledger)
