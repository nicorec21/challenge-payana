"""Contrato de salida del sistema.

Este módulo define **la** representación serializable de lo que el sistema sabe.
No es un detalle de implementación de la API: es el entregable *"salida
estructurada para la IA"* del enunciado, y la única fuente de la que se
proyectan todas las salidas.

Regla: la API, el CLI y la web **renderizan** estas estructuras; ninguno calcula
nada por su cuenta. Si el reporte del CFO y el JSON se generaran por caminos
distintos, divergirían, y el sistema afirmaría dos cosas distintas sobre el
mismo hecho. Ver ADR-0004.

Convenciones del contrato, pensadas para que un consumidor programático no
tenga que adivinar:

- **Los montos viajan en unidades menores (centavos) como enteros**, en
  `*_cents`, más una versión formateada aparte para mostrar. Nunca un float:
  un consumidor que sume floats reintroduce el error de redondeo que todo el
  sistema evita.
- Las fechas van en ISO-8601.
- Los identificadores son estables entre corridas (ver ADR-0001), así que un
  consumidor puede citarlos y volver a resolverlos después.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ..domain.explanation import Explanation
from ..domain.ledger import Ledger
from ..domain.money import Money
from ..domain.movement import Movement

CONTRACT_VERSION = "1.0"


def _money(amount: Money) -> dict[str, Any]:
    """Un monto, en la forma en que viaja siempre.

    `cents` es para operar, `formatted` para mostrar. Se mandan los dos a
    propósito: el consumidor no debería tener que conocer las convenciones de
    formato colombianas para imprimir un número, ni parsear texto para sumarlo.
    """
    return {
        "cents": amount.amount,
        "currency": amount.currency,
        "formatted": amount.format(),
    }


@dataclass(frozen=True, slots=True)
class MovementView:
    """Un movimiento tal como lo ve un consumidor externo."""

    id: str
    ledger_id: str
    source_id: str
    external_id: str
    occurred_on: str
    occurred_at: str | None
    amount: dict[str, Any]
    kind: str
    status: str
    description: str
    reference: str | None
    counterparty: str | None
    #: Puntero a la evidencia: `data/raw/...#pagina=2,y=158`. Es lo que hace
    #: que cualquier afirmación del sistema se pueda rastrear hasta un archivo.
    raw_ref: str | None
    metadata: dict[str, Any]
    #: `False` para rechazados y errores: están en el ledger para poder explicar
    #: por qué esa venta no llegó al banco, pero no mueven plata.
    counts_for_reconciliation: bool

    @classmethod
    def of(cls, movement: Movement) -> MovementView:
        return cls(
            id=movement.id,
            ledger_id=movement.ledger_id,
            source_id=movement.source_id,
            external_id=movement.external_id,
            occurred_on=movement.occurred_on.isoformat(),
            occurred_at=movement.occurred_at.isoformat() if movement.occurred_at else None,
            amount=_money(movement.amount),
            kind=movement.kind.value,
            status=movement.status.value,
            description=movement.description,
            reference=movement.reference,
            counterparty=movement.counterparty,
            raw_ref=movement.raw_ref,
            metadata=dict(movement.metadata),
            counts_for_reconciliation=movement.counts_for_reconciliation,
        )


@dataclass(frozen=True, slots=True)
class BreakdownRow:
    kind: str
    status: str
    count: int
    total: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """Estado de un ledger. Lo que se muestra sin abrir los movimientos."""

    id: str
    name: str
    currency: str
    role: str
    movement_count: int
    balance: dict[str, Any]
    date_range: tuple[str, str] | None
    breakdown: list[BreakdownRow]
    sources: list[str]

    @classmethod
    def of(cls, ledger: Ledger, sources: list[str] | None = None) -> LedgerSummary:
        from collections import Counter

        counts = Counter((m.kind.value, m.status.value) for m in ledger)
        breakdown = [
            BreakdownRow(
                kind=kind,
                status=status,
                count=n,
                total=_money(
                    Money.sum(
                        (
                            m.amount
                            for m in ledger
                            if m.kind.value == kind and m.status.value == status
                        ),
                        ledger.currency,
                    )
                ),
            )
            for (kind, status), n in sorted(counts.items())
        ]
        rango = ledger.date_range
        return cls(
            id=ledger.id,
            name=ledger.account.name,
            currency=ledger.currency,
            role=ledger.account.role,
            movement_count=len(ledger),
            balance=_money(ledger.balance()),
            date_range=(rango[0].isoformat(), rango[1].isoformat()) if rango else None,
            breakdown=breakdown,
            sources=sorted(sources or {m.source_id for m in ledger}),
        )


@dataclass(frozen=True, slots=True)
class StatementLine:
    """Una línea del extracto, en orden de documento.

    `running_balance` es el saldo que declara el banco y `expected_balance` el
    que calcula el sistema acumulando los movimientos. Que coincidan es el
    invariante de ADR-0007; mandar los dos permite **verlo**, no solo confiar.
    """

    position: int
    movement_id: str
    occurred_on: str
    description: str
    amount: dict[str, Any]
    running_balance: dict[str, Any]
    expected_balance: dict[str, Any]
    chain_ok: bool
    page: int | None
    raw_ref: str | None


@dataclass(frozen=True, slots=True)
class StatementView:
    """Un extracto reconstruido como lo muestra el banco.

    Existe porque el ledger ordena por `(fecha, id)` —determinista, ADR-0001—
    y ese no es el orden del documento. La cadena de saldos solo existe en orden
    de documento, así que sin esta vista el extracto no se puede verificar a ojo
    contra el PDF.
    """

    ledger_id: str
    period: str
    account_number: str | None
    opening_balance: dict[str, Any]
    closing_balance: dict[str, Any]
    line_count: int
    chain_intact: bool
    lines: list[StatementLine]


@dataclass(frozen=True, slots=True)
class TransactionBreakdown:
    """Una transacción de canal con su descomposición.

    Verificación visual del reparto disjunto (ADR-0008): el bruto viene de la
    API, los descuentos del CSV, y son fuentes distintas.

    **El cierre en cero NO se verifica acá cuando el canal liquida en batch.**
    Wompi consolida varias transacciones en un solo giro, así que una
    transacción aislada no puede cerrar contra él: lo que le corresponde es su
    `net_expected`, y el cierre se comprueba a nivel desembolso. El POS, en
    cambio, liquida por venta y sí cierra acá. `settlement_scope` dice cuál es
    el caso en vez de dejarlo implícito.
    """

    transaction_id: str
    ledger_id: str
    occurred_on: str
    status: str
    gross: dict[str, Any] | None
    deductions: list[MovementView]
    total_deductions: dict[str, Any]
    #: `bruto − descuentos`: lo que esta transacción aporta al giro.
    net_expected: dict[str, Any]
    #: "transaction" si el canal liquida por venta, "batch" si consolida,
    #: `None` si la venta no se liquidó (rechazada o pendiente).
    settlement_scope: str | None
    settlement: dict[str, Any] | None
    #: Solo significativo con `settlement_scope == "transaction"`.
    closes_to_zero: bool | None
    #: Desembolso declarado por Wompi. `None` en una venta rechazada: es la
    #: respuesta a "¿por qué esta venta no llegó al banco?".
    disbursement_id: Any | None
    #: `True` si los descuentos vienen declarados por la fuente; `False` si
    #: habría que inferirlos. Ver ADR-0005.
    has_declared_deductions: bool
    movements: list[MovementView]


@dataclass(frozen=True, slots=True)
class DisbursementBreakdown:
    """Un desembolso y las transacciones que lo componen.

    Acá **sí** se verifica el cierre: `Σ net_expected == |settlement|`.

    El agrupamiento no es una inferencia: usa el `disbursement_id` que la API
    de Wompi declara en cada transacción. Por eso la ambigüedad de subconjuntos
    que advierte el enunciado no aplica por esta vía.

    Cuando `Σ net_expected` no llega al giro, la diferencia son descuentos que
    todavía no tenemos declarados (solo hay CSV de 4 de 56 días). `residual` lo
    muestra en vez de esconderlo, y `deductions_complete` dice si el desembolso
    tiene el desglose completo.
    """

    disbursement_id: str
    ledger_id: str
    settled_on: str
    settlement: dict[str, Any]
    transaction_count: int
    gross_total: dict[str, Any]
    declared_deductions: dict[str, Any]
    net_expected: dict[str, Any]
    #: `Σ net_expected − |settlement|`. Cero cuando el desglose está completo.
    residual: dict[str, Any]
    closes_to_zero: bool
    deductions_complete: bool
    transaction_ids: list[str]


@dataclass(frozen=True, slots=True)
class AdjustmentView:
    """Un componente de la diferencia entre bruto y neto.

    `source` es el campo que más importa: distingue lo **observado** de lo
    **supuesto**. Un consumidor que no lo mire puede tratar una comisión
    inferida como si el canal la hubiera declarado, y no son lo mismo — medido
    sobre estos datos, declarado acierta 9/9 al centavo e inferido 47/55 con
    error de ±$0,01.
    """

    kind: str
    amount: dict[str, Any]
    #: "declared" | "inferred" | "allocated"
    source: str
    note: str


@dataclass(frozen=True, slots=True)
class AlternativeView:
    """Una hipótesis que el motor evaluó y descartó.

    Exponer lo descartado es lo que convierte un match en un argumento.
    """

    description: str
    movement_ids: list[str]
    rejected_because: str
    residual: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ExplanationView:
    """El razonamiento detrás de una conclusión, serializado.

    `rule_id` es estable y filtrable sin parsear prosa: un consumidor
    programático puede pedir "todos los findings de la regla X" sin leer texto.
    """

    rule_id: str
    summary: str
    confidence: str
    source_movement_ids: list[str]
    target_movement_ids: list[str]
    gross: dict[str, Any] | None
    net: dict[str, Any] | None
    adjustments: list[AdjustmentView]
    adjustments_total: dict[str, Any] | None
    #: `gross − ajustes == net`: si la explicación se sostiene sola.
    is_balanced: bool
    window: dict[str, str] | None
    alternatives: list[AlternativeView]
    #: Lo que ninguna regla explica. `null` cuando es cero.
    unexplained: dict[str, Any] | None

    @classmethod
    def of(cls, e: Explanation) -> ExplanationView:
        total = e.adjustments_total
        return cls(
            rule_id=e.rule_id,
            summary=e.summary,
            confidence=e.confidence.value,
            source_movement_ids=list(e.source_movement_ids),
            target_movement_ids=list(e.target_movement_ids),
            gross=_money(e.gross) if e.gross else None,
            net=_money(e.net) if e.net else None,
            adjustments=[
                AdjustmentView(
                    kind=a.kind.value,
                    amount=_money(a.amount),
                    source=a.source.value,
                    note=a.note,
                )
                for a in e.adjustments
            ],
            adjustments_total=_money(total) if total else None,
            is_balanced=e.is_balanced,
            window=(
                {"start": e.window.start.isoformat(), "end": e.window.end.isoformat(),
                 "rule": e.window.rule}
                if e.window else None
            ),
            alternatives=[
                AlternativeView(
                    description=alt.description,
                    movement_ids=list(alt.movement_ids),
                    rejected_because=alt.rejected_because,
                    residual=_money(alt.residual) if alt.residual else None,
                )
                for alt in e.alternatives
            ],
            unexplained=_money(e.unexplained) if e.unexplained else None,
        )


@dataclass(frozen=True, slots=True)
class FlowFindingView:
    """Una conclusión de la conciliación de flujo."""

    id: str
    status: str
    #: Si exige acción de alguien. `out_of_coverage` no lo es: informa una
    #: limitación del dato, no un problema de la plata.
    is_problem: bool
    occurred_on: str | None
    settlement_movement_id: str | None
    bank_movement_id: str | None
    transaction_ids: list[str]
    settlement_amount: dict[str, Any] | None
    bank_amount: dict[str, Any] | None
    difference: dict[str, Any] | None
    explanation: ExplanationView


@dataclass(frozen=True, slots=True)
class FlowReportView:
    """Resultado completo de una conciliación de flujo."""

    contract_version: str
    generated_at: str
    channel_ledger_id: str
    bank_ledger_id: str
    coverage: dict[str, Any]
    counts: dict[str, int]
    by_confidence: dict[str, int]
    matched_amount: dict[str, Any]
    #: Todo lo que ninguna regla explica, incluidos los centavos de redondeo de
    #: las conciliaciones que sí cerraron.
    unexplained_total: dict[str, Any]
    #: Solo lo atribuible a los casos problemáticos. Es el número que hay que
    #: mostrar: `unexplained_total` incluye el redondeo de matches exitosos y no
    #: coincide con la suma del detalle.
    disputed_amount: dict[str, Any]
    #: Diferencia entre los dos anteriores: el error acumulado de estimar
    #: comisiones en vez de leerlas. Acotado a un centavo por venta.
    rounding_amount: dict[str, Any]
    problem_count: int
    findings: list[FlowFindingView]


@dataclass(frozen=True, slots=True)
class SourceView:
    name: str
    ledger_id: str
    connector_id: str
    adapters: list[str]
    requires_network: bool


@dataclass(frozen=True, slots=True)
class SystemView:
    """Lo que el sistema es, de un vistazo."""

    contract_version: str
    generated_at: str
    ledgers: list[LedgerSummary]
    sources: list[SourceView]
    #: Ventanas de cobertura por ledger. Distinguen "no llegó la plata" de
    #: "no tengo datos de ese período", que se ven idénticos y significan lo
    #: contrario. Ver ADR-0008.
    coverage: dict[str, dict[str, str | None]] = field(default_factory=dict)


def to_dict(obj: Any) -> Any:
    """Serializa cualquier estructura del contrato a JSON puro."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Money):
        return _money(obj)
    return obj


def now_iso() -> str:

    return datetime.now(UTC).isoformat()


# ── conciliación contra el ERP ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ErpFindingView:
    """Una comparación línea a línea contra el libro contable.

    En una coincidencia viajan **los dos identificadores** —el del movimiento y
    el de la línea del ERP— más el nombre del asiento, que es como lo ve un
    contador. Es lo que el enunciado pide explícitamente.
    """

    id: str
    status: str
    is_problem: bool
    occurred_on: str | None
    kind: str | None
    ledger_movement_id: str | None
    book_movement_id: str | None
    #: `account.move.line.id`, para poder abrir la línea en Odoo.
    erp_line_id: str | None
    #: `WMP/2026/00001`.
    erp_move_name: str | None
    ledger_amount: dict[str, Any] | None
    book_amount: dict[str, Any] | None
    difference: dict[str, Any] | None
    explanation: ExplanationView


@dataclass(frozen=True, slots=True)
class ErpGroupView:
    """Faltantes agrupados por tipo de movimiento.

    «El ERP no registra ninguna comisión» es una conclusión; 27 findings de
    comisión suelta son ruido con la misma información.
    """

    kind: str
    count: int
    total: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ErpReportView:
    contract_version: str
    generated_at: str
    ledger_id: str
    book_ledger_id: str
    #: Cuenta del plan que representa al ledger en Odoo.
    account_code: str
    counts: dict[str, int]
    #: Fracción de movimientos del ledger que el libro registra. Responde
    #: "¿mi ERP refleja lo que pasó?" de un vistazo.
    coverage_ratio: float
    matched_amount: dict[str, Any]
    problem_count: int
    missing_in_erp_by_kind: list[ErpGroupView]
    findings: list[ErpFindingView]
