"""Explanation: por qué el sistema concluye lo que concluye.

Pieza central del diseño. La explicación NO es un string que se arma al
renderizar: es un objeto de dominio que el motor produce junto con cada
conclusión. De ahí salen las dos salidas del challenge (reporte CFO y JSON para
la IA) vía renderers distintos sobre la MISMA estructura.

Si la explicación fuera texto, las dos salidas se desincronizarían y el sistema
dejaría de ser auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .money import Money


class AdjustmentKind(StrEnum):
    """Por qué dos montos que representan lo mismo no son iguales."""

    COMMISSION = "commission"
    TAX = "tax"
    WITHHOLDING = "withholding"
    ROUNDING = "rounding"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    UNEXPLAINED = "unexplained"


class EvidenceSource(StrEnum):
    """De dónde salió un ajuste. Distinguir observado de inferido es la
    diferencia entre "el sistema sabe" y "el sistema supone"."""

    #: La fuente lo declaró explícitamente.
    DECLARED = "declared"
    #: Lo dedujimos de la diferencia bruto - neto.
    INFERRED = "inferred"
    #: Lo asignamos prorrateando un ajuste de nivel batch a un movimiento.
    ALLOCATED = "allocated"


@dataclass(frozen=True, slots=True)
class Adjustment:
    """Un componente de la diferencia entre dos montos."""

    kind: AdjustmentKind
    amount: Money
    source: EvidenceSource
    note: str = ""


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Ventana temporal que el matcher consideró."""

    start: date
    end: date
    #: Regla que la generó, ej "T+1 hábil, calendario CO, tolerancia +1 día".
    rule: str = ""

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


class Confidence(StrEnum):
    """Confianza en la conclusión.

    Escala ordinal, no probabilidad. Un score 0..1 inventado sugiere una
    precisión que no tenemos; el CFO necesita saber si puede firmar, no un
    número decimal falso.
    """

    #: Match unívoco y sin residuo inexplicado.
    EXACT = "exact"
    #: Match único bajo la regla, con ajustes inferidos coherentes.
    HIGH = "high"
    #: Match plausible pero con residuo o con alternativas descartadas por poco.
    MEDIUM = "medium"
    #: Match sugerido; requiere revisión humana.
    LOW = "low"


@dataclass(frozen=True, slots=True)
class Alternative:
    """Una hipótesis que el motor evaluó y descartó.

    Exponer lo descartado es lo que convierte un match en un argumento. Sin
    esto, el sistema afirma; con esto, el sistema justifica.
    """

    description: str
    movement_ids: tuple[str, ...]
    rejected_because: str
    #: Qué tan cerca estuvo, para que el humano juzgue si el corte fue justo.
    residual: Money | None = None


@dataclass(frozen=True, slots=True)
class Explanation:
    """El razonamiento detrás de una conclusión."""

    #: Identificador estable de la regla aplicada, ej "flow.daily_batch_t1".
    #: Estable a propósito: la IA puede filtrar por regla sin parsear prosa.
    rule_id: str
    #: Frase en español para el CFO. Redundante con los campos estructurados,
    #: y esa redundancia es intencional: el humano no debe reconstruirla.
    summary: str
    #: Movimientos del lado origen (ej: los pagos del canal).
    source_movement_ids: tuple[str, ...] = ()
    #: Movimientos del lado destino (ej: la acreditación bancaria).
    target_movement_ids: tuple[str, ...] = ()
    #: Monto bruto del lado origen.
    gross: Money | None = None
    #: Monto neto observado del lado destino.
    net: Money | None = None
    #: Descomposición de `gross - net`.
    adjustments: tuple[Adjustment, ...] = ()
    window: TimeWindow | None = None
    confidence: Confidence = Confidence.MEDIUM
    alternatives: tuple[Alternative, ...] = ()
    #: Parte de la diferencia que ninguna regla explica. Si no es cero, el
    #: sistema lo dice en vez de esconderlo dentro de "comisiones".
    unexplained: Money | None = None

    @property
    def adjustments_total(self) -> Money | None:
        if not self.adjustments:
            return None
        return Money.sum((a.amount for a in self.adjustments), self.adjustments[0].amount.currency)

    @property
    def is_balanced(self) -> bool:
        """`gross - ajustes == net`, es decir la explicación cierra."""
        if self.gross is None or self.net is None:
            return False
        total = self.adjustments_total or Money.zero(self.gross.currency)
        return (self.gross - abs(total)) == self.net

    @property
    def adjustment_ratio(self) -> Decimal | None:
        """Ajustes como fracción del bruto. Una comisión del 40% es una señal
        de que la regla matcheó mal, no de que Wompi cobra caro."""
        total = self.adjustments_total
        if total is None or self.gross is None or self.gross.is_zero:
            return None
        return abs(total).ratio_to(self.gross)


@dataclass
class ExplanationBuilder:
    """Acumula evidencia mientras la regla razona.

    Existe para que las reglas no construyan `Explanation` a mano y se olviden
    de registrar un descarte. El builder hace barato explicar; si explicar es
    caro, las reglas dejan de hacerlo.
    """

    rule_id: str
    currency: str = "COP"
    _adjustments: list[Adjustment] = field(default_factory=list)
    _alternatives: list[Alternative] = field(default_factory=list)

    def adjust(
        self,
        kind: AdjustmentKind,
        amount: Money,
        source: EvidenceSource,
        note: str = "",
    ) -> ExplanationBuilder:
        self._adjustments.append(Adjustment(kind, amount, source, note))
        return self

    def discard(
        self,
        description: str,
        movement_ids: tuple[str, ...],
        because: str,
        residual: Money | None = None,
    ) -> ExplanationBuilder:
        self._alternatives.append(Alternative(description, movement_ids, because, residual))
        return self

    def build(
        self,
        summary: str,
        *,
        source_ids: tuple[str, ...] = (),
        target_ids: tuple[str, ...] = (),
        gross: Money | None = None,
        net: Money | None = None,
        window: TimeWindow | None = None,
        confidence: Confidence = Confidence.MEDIUM,
        unexplained: Money | None = None,
    ) -> Explanation:
        return Explanation(
            rule_id=self.rule_id,
            summary=summary,
            source_movement_ids=source_ids,
            target_movement_ids=target_ids,
            gross=gross,
            net=net,
            adjustments=tuple(self._adjustments),
            window=window,
            confidence=confidence,
            alternatives=tuple(self._alternatives),
            unexplained=unexplained,
        )
