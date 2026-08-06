"""Ledger: flujo cronológico de movimientos de una sola cuenta."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator

from .money import Money
from .movement import Movement, MovementKind, MovementStatus


@dataclass(frozen=True, slots=True)
class Account:
    """La cuenta que el ledger representa."""

    id: str
    name: str
    currency: str = "COP"
    #: Rol en el flujo de fondos. Determina qué conciliaciones aplican.
    #: "channel" = procesador que cobra y luego liquida (Wompi, POS).
    #: "bank" = cuenta bancaria destino de las liquidaciones.
    role: str = "channel"


@dataclass
class Ledger:
    """Colección ordenada de movimientos de una cuenta, con saldo acumulado.

    Mutable a propósito: la ingesta la va llenando. La inmutabilidad vive en el
    `Movement`, que es donde importa.
    """

    account: Account
    _movements: list[Movement] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)

    @property
    def id(self) -> str:
        return self.account.id

    @property
    def currency(self) -> str:
        return self.account.currency

    def add(self, movement: Movement) -> bool:
        """Agrega un movimiento. Devuelve False si ya estaba (idempotencia).

        La deduplicación es por `(source_id, external_id)`, no por contenido:
        dos cobros distintos del mismo monto el mismo día son dos movimientos.
        """
        if movement.ledger_id != self.account.id:
            raise ValueError(
                f"Movimiento de ledger '{movement.ledger_id}' agregado a '{self.account.id}'"
            )
        if movement.amount.currency != self.currency:
            raise ValueError(
                f"Moneda {movement.amount.currency} en ledger {self.currency}"
            )
        if movement.idempotency_key in self._seen:
            return False
        self._seen.add(movement.idempotency_key)
        self._movements.append(movement)
        return True

    def extend(self, movements: Iterable[Movement]) -> int:
        """Agrega varios. Devuelve cuántos eran nuevos."""
        return sum(1 for m in movements if self.add(m))

    @property
    def movements(self) -> list[Movement]:
        """Movimientos en orden cronológico estable.

        Desempate por `id` (determinista) y no por orden de inserción, para que
        el reporte no dependa del orden en que se leyeron los archivos.
        """
        return sorted(self._movements, key=lambda m: (m.occurred_on, m.id))

    def __iter__(self) -> Iterator[Movement]:
        return iter(self.movements)

    def __len__(self) -> int:
        return len(self._movements)

    # -- consultas que usa el motor de conciliación -----------------------

    def balance(self, up_to: date | None = None) -> Money:
        return Money.sum(
            (m.amount for m in self._movements if up_to is None or m.occurred_on <= up_to),
            self.currency,
        )

    def between(self, start: date, end: date) -> list[Movement]:
        """Movimientos en `[start, end]`, ambos inclusive."""
        return [m for m in self.movements if start <= m.occurred_on <= end]

    def on(self, day: date) -> list[Movement]:
        return [m for m in self.movements if m.occurred_on == day]

    def of_kind(self, *kinds: MovementKind) -> list[Movement]:
        wanted = set(kinds)
        return [m for m in self.movements if m.kind in wanted]

    def approved(self) -> list[Movement]:
        return [m for m in self.movements if m.status is MovementStatus.APPROVED]

    def group_by_day(self) -> dict[date, list[Movement]]:
        grouped: dict[date, list[Movement]] = defaultdict(list)
        for m in self.movements:
            grouped[m.occurred_on].append(m)
        return dict(grouped)

    def by_id(self, movement_id: str) -> Movement | None:
        return next((m for m in self._movements if m.id == movement_id), None)

    @property
    def date_range(self) -> tuple[date, date] | None:
        if not self._movements:
            return None
        days = [m.occurred_on for m in self._movements]
        return (min(days), max(days))
