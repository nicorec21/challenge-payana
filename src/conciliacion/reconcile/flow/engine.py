"""Conciliación de flujo: ¿la plata que el canal prometió llegó al banco?

No valida el ERP. Reconstruye el flujo real de fondos entre canal y banco.

## Los dos saltos

El enunciado describe un salto (canal → banco). Contrastado contra los datos
reales resultaron ser dos, con dificultad muy distinta:

    ventas del día D
          │  T+1 hábil, el canal consolida y descuenta
          ▼
    desembolso (batch)
          │  mismo día — verificado 10/10 en abril 2026
          ▼
    crédito en el extracto bancario

**El primer salto no se busca: el canal lo declara.** Cada transacción de la API
de Wompi trae su `disbursement_id`, así que agrupar ventas en su liquidación es
un `GROUP BY` sobre un dato observado. La ambigüedad que advierte el enunciado
—"varios subconjuntos de ventas que suman lo mismo"— no aplica por esta vía, y
el motor lo dice explícitamente en la explicación en vez de fingir una búsqueda.

**El segundo salto sí se busca**, por monto dentro de una ventana de días
hábiles.

## No poder calcular no es haber calculado cero

El desglose se infiere con el tarifario del medio de pago de **cada venta**. Una
venta cuyo medio no tiene tarifario no aporta descuentos inferidos: aporta su
bruto entero al residuo. Codificar ese caso como cero —que es lo que hacía— le
hacía informar al motor *"todo el dinero está explicado"* sobre un giro cuya
composición no había podido verificar, y con confianza alta. Ver
`_split_by_schedule`, `_residual` y `_confidence`.

## Qué NO se usa para matchear

La **descripción del banco**. Cambia a mitad del período (`PAGO DE PROV WOMPI`
→ `PAGO DE TERC WOMPI` el 14/04/2026), y un matcher que la use como llave
pierde 53 de 58 líneas. Se usa solo para acotar qué créditos huérfanos vale la
pena reportar, y eso queda marcado como heurístico en la explicación.

El **número de cuenta** tampoco: la API declara una cuenta y el extracto es de
otra (ver ADR-0007).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ...config import (
    FLOW_AMOUNT_TOLERANCE,
    INFERENCE_TOLERANCE_PER_TRANSACTION,
    FeeSchedule,
    SettlementPolicy,
    bank_hints_for,
    fee_schedule_for,
    settlement_policy_for,
)
from ...domain.explanation import (
    AdjustmentKind,
    Confidence,
    EvidenceSource,
    ExplanationBuilder,
    TimeWindow,
)
from ...domain.ledger import Ledger
from ...domain.money import Money
from ...domain.movement import Movement, MovementKind
from ..calendar import BusinessCalendar
from .findings import Coverage, FlowFinding, FlowReport, FlowStatus

RULE_DECLARED_BATCH = "flow.settlement_declared_batch"
RULE_NO_BANK_CREDIT = "flow.settlement_without_bank_credit"
RULE_OUT_OF_COVERAGE = "flow.out_of_coverage"
RULE_ORPHAN_BANK = "flow.bank_credit_without_settlement"


@dataclass(frozen=True, slots=True)
class _Batch:
    """Un giro del canal con las ventas que lo componen."""

    settlement: Movement
    transactions: list[Movement]
    deductions: list[Movement]

    @property
    def gross(self) -> Money:
        return Money.sum(
            (t.amount for t in self.transactions),
            self.settlement.amount.currency,
        )

    @property
    def declared_deductions(self) -> Money:
        return Money.sum(
            (abs(d.amount) for d in self.deductions),
            self.settlement.amount.currency,
        )

    @property
    def has_declared_deductions(self) -> bool:
        """Si el desglose fiscal está declarado para TODAS sus ventas.

        Parcial no alcanza: mezclar descuentos declarados de unas ventas con
        inferidos de otras produce un número que no es ni una cosa ni la otra.
        """
        con_desglose = {d.metadata.get("transaction_id") for d in self.deductions}
        return bool(self.transactions) and all(
            t.external_id in con_desglose for t in self.transactions
        )


def reconcile_flow(
    channel: Ledger,
    bank: Ledger,
    *,
    calendar: BusinessCalendar | None = None,
    policy: SettlementPolicy | None = None,
    tolerance: Money = FLOW_AMOUNT_TOLERANCE,
    coverage: Coverage | None = None,
) -> FlowReport:
    """Concilia los giros de un canal contra los créditos de un banco.

    `coverage` declara qué período cubre efectivamente cada fuente. Es lo que
    separa "falta plata" de "falta data".

    Si no se pasa, se deriva del rango de movimientos observados —y esa
    derivación es **conservadora, no exacta**: un extracto sin movimientos en
    marzo es indistinguible de un marzo que nadie bajó. Quien tenga el dato
    bueno es la ingesta (`IngestionReport.requested_window`), así que en la
    corrida real conviene pasarlo explícito y dejar la derivación como último
    recurso.
    """
    calendar = calendar or BusinessCalendar("CO")
    policy = policy or settlement_policy_for(channel.id)
    coverage = coverage or Coverage(channel=channel.date_range, bank=bank.date_range)

    report = FlowReport(
        channel_ledger_id=channel.id, bank_ledger_id=bank.id, coverage=coverage
    )

    batches = _build_batches(channel)
    disponibles = [m for m in bank if m.kind is MovementKind.BANK_CREDIT]
    usados: set[str] = set()

    for batch in batches:
        finding = _match_batch(
            batch, disponibles, usados, coverage, calendar, policy, tolerance
        )
        report.findings.append(finding)
        if finding.bank_movement_id:
            usados.add(finding.bank_movement_id)

    report.findings.extend(
        _orphan_bank_credits(channel.id, disponibles, usados, coverage)
    )
    report.findings.sort(key=lambda f: (f.occurred_on or date.min, f.status.value))
    return report


# ── construcción de batches ─────────────────────────────────────────────────


def _build_batches(channel: Ledger) -> list[_Batch]:
    """Agrupa ventas y descuentos alrededor de cada giro.

    Usa `metadata["disbursement_id"]`, que el canal declara. Los giros sin ese
    dato quedan como batch sin transacciones, que es información: significa que
    hubo un giro cuyo origen no podemos reconstruir.
    """
    ventas: dict[str, list[Movement]] = defaultdict(list)
    descuentos_por_tx: dict[str, list[Movement]] = defaultdict(list)
    settlements: list[Movement] = []

    for m in channel:
        if m.kind is MovementKind.SETTLEMENT:
            settlements.append(m)
        elif m.kind is MovementKind.PAYMENT and m.counts_for_reconciliation:
            if (did := m.metadata.get("disbursement_id")) is not None:
                ventas[str(did)].append(m)
        elif m.kind in (MovementKind.FEE, MovementKind.TAX) and (
            tx := m.metadata.get("transaction_id")
        ) is not None:
            descuentos_por_tx[str(tx)].append(m)

    batches: list[_Batch] = []
    for settlement in settlements:
        did = str(settlement.metadata.get("disbursement_id", settlement.external_id))
        txs = ventas.get(did, [])
        deds = [d for t in txs for d in descuentos_por_tx.get(t.external_id, [])]
        batches.append(_Batch(settlement=settlement, transactions=txs, deductions=deds))
    return batches


# ── matching ────────────────────────────────────────────────────────────────


def _match_batch(
    batch: _Batch,
    bank_credits: list[Movement],
    usados: set[str],
    coverage: Coverage,
    calendar: BusinessCalendar,
    policy: SettlementPolicy,
    tolerance: Money,
) -> FlowFinding:
    giro = abs(batch.settlement.amount)
    dia = batch.settlement.occurred_on
    window = _window(dia, calendar, policy)

    candidatos = [
        m
        for m in bank_credits
        if m.id not in usados
        and window.contains(m.occurred_on)
        and abs((m.amount - giro).amount) <= tolerance.amount
    ]

    if not candidatos:
        return _no_match(batch, giro, dia, window, coverage)

    elegido = min(
        candidatos,
        key=lambda m: (abs((m.amount - giro).amount), abs((m.occurred_on - dia).days), m.id),
    )
    descartados = [m for m in candidatos if m.id != elegido.id]
    return _matched(batch, elegido, descartados, giro, dia, window, calendar, policy)


def _window(
    settlement_day: date, calendar: BusinessCalendar, policy: SettlementPolicy
) -> TimeWindow:
    """Ventana de búsqueda del crédito, en días hábiles desde el giro.

    No es igualdad de fecha aunque los 10 casos verificados hayan caído el mismo
    día: hay evidencia de al menos un desfasaje mayor (la venta del 29-04 21:22
    liquidada el 04-05), y una ventana rígida haría que ese caso no concilie
    nunca. El monto decide cuál candidato gana; la ventana solo los genera.
    """
    start = calendar.shift(settlement_day, policy.min_business_days)
    end = calendar.shift(settlement_day, policy.max_business_days)
    return TimeWindow(
        start=min(start, settlement_day),
        end=end,
        rule=(
            f"[{policy.min_business_days}, {policy.max_business_days}] días hábiles "
            f"desde el giro, calendario {calendar.country}"
        ),
    )


def _matched(
    batch: _Batch,
    elegido: Movement,
    descartados: list[Movement],
    giro: Money,
    dia: date,
    window: TimeWindow,
    calendar: BusinessCalendar,
    policy: SettlementPolicy,
) -> FlowFinding:
    builder = ExplanationBuilder(RULE_DECLARED_BATCH, giro.currency)
    _register_adjustments(builder, batch)

    inexplicado = _residual(batch, giro)
    dias_habiles = calendar.business_days_between(dia, elegido.occurred_on)
    confianza = _confidence(batch, descartados, inexplicado, dias_habiles, policy)

    for otro in descartados:
        builder.discard(
            description=f"crédito del {otro.occurred_on} por {otro.amount} — «{otro.description}»",
            movement_ids=(otro.id,),
            because=(
                "mismo monto y misma ventana que el elegido; se prefirió el más "
                "cercano en fecha. Requiere revisión humana."
            ),
            residual=otro.amount - giro,
        )

    explanation = builder.build(
        summary=_summary(batch, elegido, giro, dias_habiles),
        source_ids=tuple(t.id for t in batch.transactions) + (batch.settlement.id,),
        target_ids=(elegido.id,),
        gross=batch.gross if batch.transactions else None,
        net=elegido.amount,
        window=window,
        confidence=confianza,
        unexplained=inexplicado if not inexplicado.is_zero else None,
    )

    return FlowFinding(
        status=FlowStatus.AMBIGUOUS if descartados else FlowStatus.MATCHED,
        explanation=explanation,
        settlement_movement_id=batch.settlement.id,
        bank_movement_id=elegido.id,
        transaction_ids=tuple(t.id for t in batch.transactions),
        occurred_on=dia,
        settlement_amount=giro,
        bank_amount=elegido.amount,
    )


def _no_match(
    batch: _Batch, giro: Money, dia: date, window: TimeWindow, coverage: Coverage
) -> FlowFinding:
    """Sin candidato. Distinguir falta de plata de falta de datos.

    Es la distinción más importante del reporte: se ven idénticas y significan
    lo contrario. Reportar 47 faltantes que no faltan destruye la confianza en
    el informe entero.
    """
    cubierto = coverage.covers_bank(window.start) or coverage.covers_bank(window.end)
    builder = ExplanationBuilder(
        RULE_NO_BANK_CREDIT if cubierto else RULE_OUT_OF_COVERAGE, giro.currency
    )
    _register_adjustments(builder, batch)

    if cubierto:
        summary = (
            f"El canal declara un giro de {giro} el {dia} y no hay ningún crédito "
            f"por ese monto en el extracto dentro de {window.rule}. "
            f"La plata salió del canal y no se encontró en el banco."
        )
        status, confianza = FlowStatus.UNMATCHED_SETTLEMENT, Confidence.HIGH
    else:
        rango = coverage.bank
        summary = (
            f"Giro de {giro} el {dia}. No se puede concluir: el extracto bancario "
            f"cubre {rango[0]} a {rango[1]}" if rango else
            f"Giro de {giro} el {dia}. No se puede concluir: no hay extracto bancario cargado"
        ) + ". No es un faltante de plata sino de datos."
        status, confianza = FlowStatus.OUT_OF_COVERAGE, Confidence.HIGH

    explanation = builder.build(
        summary=summary,
        source_ids=tuple(t.id for t in batch.transactions) + (batch.settlement.id,),
        gross=batch.gross if batch.transactions else None,
        window=window,
        confidence=confianza,
        unexplained=giro if cubierto else None,
    )

    return FlowFinding(
        status=status,
        explanation=explanation,
        settlement_movement_id=batch.settlement.id,
        transaction_ids=tuple(t.id for t in batch.transactions),
        occurred_on=dia,
        settlement_amount=giro,
    )


def _orphan_bank_credits(
    channel_id: str,
    bank_credits: list[Movement],
    usados: set[str],
    coverage: Coverage,
) -> list[FlowFinding]:
    """Créditos bancarios que parecen del canal y ningún giro explica.

    El universo se acota por **descripción**, que es una heurística y queda
    declarado como tal en la explicación. Sin acotar, esto reportaría los 368
    movimientos bancarios ajenos al canal (nómina, impuestos, servicios) y el
    reporte sería inservible.

    La descripción se usa acá para *decidir qué mirar*, nunca para matchear.
    """
    hints = bank_hints_for(channel_id)
    if not hints:
        return []

    salida: list[FlowFinding] = []
    for m in bank_credits:
        if m.id in usados:
            continue
        if not any(h in m.description.upper() for h in hints):
            continue

        cubierto = coverage.covers_channel(m.occurred_on)
        builder = ExplanationBuilder(
            RULE_ORPHAN_BANK if cubierto else RULE_OUT_OF_COVERAGE, m.amount.currency
        )
        if cubierto:
            summary = (
                f"Crédito de {m.amount} el {m.occurred_on} («{m.description}») que "
                f"parece del canal {channel_id}, pero ningún giro declarado lo explica. "
                f"Entró plata sin origen identificado."
            )
            status = FlowStatus.UNMATCHED_BANK
        else:
            rango = coverage.channel
            summary = (
                f"Crédito de {m.amount} el {m.occurred_on} («{m.description}»). "
                f"No se puede concluir: los datos del canal cubren "
                f"{rango[0]} a {rango[1]}. Falta data, no plata."
                if rango
                else f"Crédito de {m.amount} el {m.occurred_on}. Sin datos del canal."
            )
            status = FlowStatus.OUT_OF_COVERAGE

        builder.discard(
            description="detección por descripción del extracto",
            movement_ids=(m.id,),
            because=(
                "la descripción es una heurística para acotar qué revisar, no una "
                "llave: cambió a mitad del período (PAGO DE PROV → PAGO DE TERC). "
                "Un crédito del canal con otra descripción no aparecería acá."
            ),
        )

        salida.append(
            FlowFinding(
                status=status,
                explanation=builder.build(
                    summary=summary,
                    target_ids=(m.id,),
                    net=m.amount,
                    confidence=Confidence.MEDIUM if cubierto else Confidence.HIGH,
                    unexplained=m.amount if cubierto else None,
                ),
                bank_movement_id=m.id,
                occurred_on=m.occurred_on,
                bank_amount=m.amount,
            )
        )
    return salida


# ── explicación ─────────────────────────────────────────────────────────────


def _split_by_schedule(
    batch: _Batch,
) -> tuple[list[tuple[Movement, FeeSchedule]], list[Movement]]:
    """Separa las ventas del batch según tengamos tarifario para su medio de pago.

    Por venta y no por batch. Tomar el medio de pago de la primera transacción
    hacía que un batch mixto se explicara distinto según cuál venta quedara
    primera en el orden interno —que es el `sha256` del id y no significa nada—:
    el mismo desembolso salía con confianza baja o alta según el sorteo.
    """
    con_tarifa: list[tuple[Movement, FeeSchedule]] = []
    sin_tarifa: list[Movement] = []
    for t in batch.transactions:
        medio = str(t.metadata.get("payment_method_type") or "")
        if (schedule := fee_schedule_for(medio, t.occurred_on)) is not None:
            con_tarifa.append((t, schedule))
        else:
            sin_tarifa.append(t)
    return con_tarifa, sin_tarifa


def _register_adjustments(builder: ExplanationBuilder, batch: _Batch) -> None:
    """Anota de dónde sale cada descuento: leído o calculado.

    Es la distinción que le da contenido a `Confidence`. Medido sobre los datos
    del challenge: los declarados aciertan 9/9 al centavo, los inferidos 47/55
    con error acotado a ±$0,01 por desembolso.
    """
    if batch.has_declared_deductions:
        moneda = batch.settlement.amount.currency
        por_tipo: dict[AdjustmentKind, Money] = {}
        for d in batch.deductions:
            kind = _adjustment_kind(d)
            por_tipo[kind] = por_tipo.get(kind, Money.zero(moneda)) + abs(d.amount)
        # En el orden en que se aplican, no en el que llegaron del CSV: la
        # escalera bruto → neto se lee como la fórmula, y dos corridas sobre la
        # misma data producen el mismo informe.
        for kind in AdjustmentKind:
            if (monto := por_tipo.get(kind)) is None:
                continue
            builder.adjust(
                kind, monto, EvidenceSource.DECLARED,
                note="declarado por el canal en el reporte de desembolso",
            )
        return

    con_tarifa, sin_tarifa = _split_by_schedule(batch)

    if not con_tarifa:
        if batch.transactions:
            builder.adjust(
                AdjustmentKind.UNEXPLAINED,
                batch.gross - abs(batch.settlement.amount),
                EvidenceSource.INFERRED,
                note="sin tarifario conocido para este medio de pago: no se puede desglosar",
            )
        return

    moneda = batch.settlement.amount.currency
    comision = Money.sum((s.commission(t.amount) for t, s in con_tarifa), moneda)
    iva = Money.sum((s.iva(t.amount) for t, s in con_tarifa), moneda)
    retefuente = Money.sum((s.retefuente(t.amount) for t, s in con_tarifa), moneda)

    schedules = {s for _, s in con_tarifa}
    if len(schedules) == 1:
        schedule = next(iter(schedules))
        detalle = f"({schedule.commission_rate:.4%} + {schedule.commission_fixed} fijo)"
        nota_iva = (
            f"{schedule.iva_rate:.0%} sobre la comisión sin truncar; calcularlo "
            f"sobre la comisión ya truncada falla en 2 de las 9 filas declaradas"
        )
        nota_rete = f"retención en la fuente ({schedule.retefuente_rate:.2%} del bruto)"
    else:
        medios = ", ".join(sorted(s.payment_method for s in schedules))
        detalle = f"(un tarifario por medio de pago: {medios})"
        nota_iva = "IVA sobre la comisión sin truncar, con el tarifario de cada venta"
        nota_rete = "retención en la fuente, con el tarifario de cada venta"

    nota = (
        f"inferido con el tarifario vigente {detalle}; el canal no declaró el "
        f"desglose de este desembolso"
    )
    builder.adjust(AdjustmentKind.COMMISSION, comision, EvidenceSource.INFERRED, note=nota)
    builder.adjust(AdjustmentKind.TAX, iva, EvidenceSource.INFERRED, note=nota_iva)
    builder.adjust(
        AdjustmentKind.WITHHOLDING, retefuente, EvidenceSource.INFERRED, note=nota_rete
    )

    if sin_tarifa:
        # El desglose es parcial. Lo que no se pudo desglosar se nombra, en vez
        # de repartirse entre los descuentos inferidos: atribuirle a la comisión
        # una plata cuya regla no conocemos es inventar evidencia.
        builder.adjust(
            AdjustmentKind.UNEXPLAINED,
            _residual(batch, abs(batch.settlement.amount)),
            EvidenceSource.INFERRED,
            note=(
                f"{len(sin_tarifa)} de {len(batch.transactions)} venta(s) sin tarifario "
                f"conocido para su medio de pago: su desglose no se pudo calcular"
            ),
        )


def _adjustment_kind(movement: Movement) -> AdjustmentKind:
    columna = str(movement.metadata.get("deduction", "")).lower()
    if movement.kind is MovementKind.FEE:
        return AdjustmentKind.COMMISSION
    if "iva" in columna:
        return AdjustmentKind.TAX
    return AdjustmentKind.WITHHOLDING


def _residual(batch: _Batch, giro: Money) -> Money:
    """Parte del bruto que ninguna regla explica.

    Con desglose declarado esto debería ser cero. Con desglose inferido puede
    quedar un residuo de centavos, porque la fórmula del canal trunca en una
    etapa distinta a la que modelamos.

    Una venta sin tarifario para su medio de pago **aporta su bruto entero**, no
    cero: de esa venta no sabemos cuánto se descontó, así que todo lo que le
    pasó queda sin explicar. Devolver cero equivalía a afirmar que no hubo
    descuentos, y con eso el motor conciliaba un giro cuya composición no había
    verificado y lo informaba como dinero explicado.
    """
    if not batch.transactions:
        return Money.zero(giro.currency)
    if batch.has_declared_deductions:
        return batch.gross - batch.declared_deductions - giro

    con_tarifa, sin_tarifa = _split_by_schedule(batch)
    esperado = Money.sum(
        [s.expected_net(t.amount) for t, s in con_tarifa]
        + [t.amount for t in sin_tarifa],
        giro.currency,
    )
    return esperado - giro


def _confidence(
    batch: _Batch,
    descartados: list[Movement],
    inexplicado: Money,
    dias_habiles: int,
    policy: SettlementPolicy,
) -> Confidence:
    """Escala de acción, no probabilidad.

    Cada señal débil baja un nivel. Deliberadamente conservador: es preferible
    que el CFO revise un match bueno a que firme uno dudoso.
    """
    if descartados:
        return Confidence.LOW

    tolerancia = INFERENCE_TOLERANCE_PER_TRANSACTION.amount * max(len(batch.transactions), 1)
    cierra = abs(inexplicado.amount) <= (
        0 if batch.has_declared_deductions else tolerancia
    )
    puntual = dias_habiles == policy.settlement_lag_business_days - 1 or dias_habiles == 0

    if not batch.transactions:
        # Giro sin ventas que lo compongan: el monto coincide pero no se puede
        # explicar de qué está hecho.
        return Confidence.MEDIUM

    if not batch.has_declared_deductions and _split_by_schedule(batch)[1]:
        # Alguna venta sin tarifario. El monto puede cerrar igual —si el canal
        # giró el bruto, o por casualidad— pero no verificamos de qué está
        # hecho el giro. Nunca "alta": alta significa que el desglose se estimó
        # con una regla conocida, y acá no hay regla.
        return Confidence.MEDIUM if cierra else Confidence.LOW

    if batch.has_declared_deductions and cierra and puntual:
        return Confidence.EXACT
    if cierra and puntual:
        return Confidence.HIGH
    if cierra:
        return Confidence.MEDIUM
    return Confidence.LOW


def _summary(batch: _Batch, elegido: Movement, giro: Money, dias_habiles: int) -> str:
    if not batch.transactions:
        return (
            f"El crédito de {elegido.amount} del {elegido.occurred_on} corresponde al "
            f"giro de {giro} declarado por el canal. No hay ventas cargadas que lo "
            f"compongan: el período de origen está fuera de los datos ingeridos."
        )

    if batch.has_declared_deductions:
        origen = "declaradas por el canal"
    elif (sin_tarifa := _split_by_schedule(batch)[1]):
        origen = (
            f"inferidas del tarifario, salvo {len(sin_tarifa)} venta(s) cuyo medio "
            f"de pago no tiene tarifario conocido"
        )
    else:
        origen = "inferidas del tarifario"
    cuando = "el mismo día" if dias_habiles == 0 else f"{dias_habiles} día(s) hábil(es) después"
    return (
        f"El crédito de {elegido.amount} del {elegido.occurred_on} es la liquidación de "
        f"{len(batch.transactions)} venta(s) por {batch.gross} bruto, menos comisiones e "
        f"impuestos ({origen}). El canal declaró el giro el {batch.settlement.occurred_on} "
        f"y el banco lo acreditó {cuando}. El agrupamiento de ventas no se infirió: "
        f"cada venta declara a qué desembolso pertenece."
    )
