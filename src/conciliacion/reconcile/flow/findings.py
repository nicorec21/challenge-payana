"""Resultados de la conciliación de flujo canal → banco.

Un `FlowFinding` es **una afirmación del sistema con su explicación adjunta**.
No existe un finding sin `Explanation`: si el motor no puede explicar por qué
concluye algo, no lo concluye.

La distinción entre estados es lo que hace útil el reporte. En particular,
`UNMATCHED_SETTLEMENT` y `OUT_OF_COVERAGE` se ven idénticos en los datos —un
giro sin crédito bancario— y significan lo contrario:

- `UNMATCHED_SETTLEMENT` → **falta plata**. Es una alerta.
- `OUT_OF_COVERAGE`      → **falta data**. Es una nota al pie.

Confundirlos produce falsos positivos que destruyen la confianza en el reporte:
el CFO ve 47 "faltantes" que no faltan y deja de mirar el informe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from ...domain.explanation import Confidence, Explanation
from ...domain.money import Money


class FlowStatus(StrEnum):
    """Qué concluyó el motor sobre un giro o un crédito."""

    #: El giro del canal se encontró en el extracto bancario.
    MATCHED = "matched"

    #: Hay giro pero no hay crédito bancario que le corresponda, **y el período
    #: está cubierto por ambas fuentes**. Falta plata: es una alerta.
    UNMATCHED_SETTLEMENT = "unmatched_settlement"

    #: Hay un crédito en el banco que parece del canal pero ningún giro lo
    #: explica. Plata que entró sin origen identificado.
    UNMATCHED_BANK = "unmatched_bank"

    #: No se puede concluir porque una de las dos fuentes no cubre ese período.
    #: NO es un faltante.
    OUT_OF_COVERAGE = "out_of_coverage"

    #: Más de un crédito bancario podría corresponder al giro. El motor elige
    #: uno y expone los descartados; requiere revisión humana.
    AMBIGUOUS = "ambiguous"

    @property
    def is_problem(self) -> bool:
        """Si esto exige acción de alguien.

        `OUT_OF_COVERAGE` no lo es: informa una limitación del dato, no un
        problema de la plata.
        """
        return self in (
            FlowStatus.UNMATCHED_SETTLEMENT,
            FlowStatus.UNMATCHED_BANK,
            FlowStatus.AMBIGUOUS,
        )


@dataclass(frozen=True, slots=True)
class FlowFinding:
    """Una conclusión del motor de flujo, con su explicación."""

    status: FlowStatus
    explanation: Explanation

    #: Movimiento `SETTLEMENT` del canal, si el finding parte de un giro.
    settlement_movement_id: str | None = None
    #: Movimiento `BANK_CREDIT` del banco, si se encontró contraparte.
    bank_movement_id: str | None = None
    #: Ventas del canal que componen el giro.
    transaction_ids: tuple[str, ...] = ()

    #: Fecha del giro (o del crédito, si el finding parte del banco).
    occurred_on: date | None = None
    #: Monto del giro declarado por el canal.
    settlement_amount: Money | None = None
    #: Monto acreditado en el banco.
    bank_amount: Money | None = None

    @property
    def rule_id(self) -> str:
        return self.explanation.rule_id

    @property
    def confidence(self) -> Confidence:
        return self.explanation.confidence

    @property
    def difference(self) -> Money | None:
        """`banco − canal`. Cero en un match limpio."""
        if self.settlement_amount is None or self.bank_amount is None:
            return None
        return self.bank_amount - self.settlement_amount


@dataclass(frozen=True, slots=True)
class Coverage:
    """Ventana de datos efectivamente disponible por ledger.

    Es lo que permite distinguir "falta plata" de "falta data". Sin esto, un
    desembolso de mayo cuyo extracto bancario no bajamos se reporta como
    dinero perdido.
    """

    channel: tuple[date, date] | None
    bank: tuple[date, date] | None

    def covers_bank(self, day: date) -> bool:
        return self.bank is not None and self.bank[0] <= day <= self.bank[1]

    def covers_channel(self, day: date) -> bool:
        return self.channel is not None and self.channel[0] <= day <= self.channel[1]

    @property
    def overlap(self) -> tuple[date, date] | None:
        """Período en el que ambas fuentes tienen datos.

        Fuera de la intersección, el sistema no puede afirmar nada sobre si la
        plata llegó: solo puede decir que no sabe.
        """
        if self.channel is None or self.bank is None:
            return None
        start = max(self.channel[0], self.bank[0])
        end = min(self.channel[1], self.bank[1])
        return (start, end) if start <= end else None


@dataclass
class FlowReport:
    """Resultado completo de una corrida de conciliación de flujo."""

    channel_ledger_id: str
    bank_ledger_id: str
    coverage: Coverage
    findings: list[FlowFinding] = field(default_factory=list)

    def of(self, *statuses: FlowStatus) -> list[FlowFinding]:
        wanted = set(statuses)
        return [f for f in self.findings if f.status in wanted]

    @property
    def problems(self) -> list[FlowFinding]:
        return [f for f in self.findings if f.status.is_problem]

    @property
    def unverified(self) -> list[FlowFinding]:
        """Giros que cerraron por monto y cuyo desglose no se pudo verificar.

        No son problemas: la plata salió del canal y entró al banco por el mismo
        importe. Pero tampoco son conciliaciones limpias — de qué está hecho ese
        giro es una pregunta abierta, típicamente porque alguna venta usó un
        medio de pago sin tarifario.

        Existe para que el residuo de estos casos no se sume al redondeo de la
        inferencia: uno vale un centavo por venta y el otro puede valer todo el
        giro, y presentarlos juntos convierte un hallazgo en una nota al pie.
        """
        return [
            f
            for f in self.findings
            if f.status is FlowStatus.MATCHED
            and f.confidence in (Confidence.LOW, Confidence.MEDIUM)
            and f.explanation.unexplained
        ]

    def counts(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(f.status.value for f in self.findings))

    def by_confidence(self) -> dict[str, int]:
        from collections import Counter

        return dict(Counter(f.confidence.value for f in self.findings))

    def matched_amount(self, currency: str = "COP") -> Money:
        return Money.sum(
            (f.bank_amount for f in self.of(FlowStatus.MATCHED) if f.bank_amount),
            currency,
        )

    def unexplained_total(self, currency: str = "COP") -> Money:
        """Suma de lo que ninguna regla logra explicar.

        Que sea cero es una afirmación fuerte; que no lo sea es información,
        no un fracaso.
        """
        return Money.sum(
            (f.explanation.unexplained for f in self.findings if f.explanation.unexplained),
            currency,
        )
