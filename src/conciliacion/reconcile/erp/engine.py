"""Conciliación contra el system of record: ¿el ERP refleja lo que pasó?

Pregunta **contable**, distinta de la de flujo. Acá no se infiere de qué canal
vino la plata —eso ya lo resolvió la Fase 2—: se compara cada ledger contra su
libro formal, línea por línea.

## El libro es otro ledger

Las líneas de Odoo se ingieren por el mismo pipeline que todo lo demás y quedan
en un ledger espejo (`wompi` ↔ `wompi_erp`). Así la Fase 3 es comparar dos
ledgers y no hace falta ningún concepto nuevo.

La proyección de partida doble a partida simple es una línea —`debit − credit`
sobre la cuenta del ledger— porque ambas cuentas son de tipo `asset_cash`.
Ver ADR-0011.

## La llave de match se decide con los datos, no a mano

`account.move.line.ref` sirve como llave **solo cuando es específica**. En el
libro de Wompi hay 41 refs únicas sobre 51 líneas: las del diario de ventas son
la referencia de la transacción, pero las 11 acreditaciones comparten el texto
`"Acreditación Wompi"`.

En vez de hardcodear cuál es cuál, el motor **cuenta**: una referencia que
aparece más de una vez en el libro no es llave, y esas líneas caen al match por
monto y fecha. Si mañana el contador cambia el texto, la regla sigue valiendo.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from ...config import erp_unrepresentable_kinds
from ...domain.explanation import Confidence, ExplanationBuilder, TimeWindow
from ...domain.ledger import Ledger
from ...domain.movement import Movement, MovementStatus
from .findings import ErpFinding, ErpReport, ErpStatus

RULE_BY_REFERENCE = "erp.matched_by_reference"
RULE_BY_AMOUNT_DATE = "erp.matched_by_amount_and_date"
RULE_AMOUNT_MISMATCH = "erp.amount_mismatch"
RULE_MISSING_IN_ERP = "erp.missing_in_erp"
RULE_NO_ACCOUNT_IN_CHART = "erp.no_account_in_chart"
RULE_MISSING_IN_LEDGER = "erp.missing_in_ledger"
RULE_NOT_POSTED = "erp.entry_not_posted"

#: Tolerancia de fecha al emparejar por monto. El asiento contable puede llevar
#: la fecha del hecho o la de registración; unos días de margen evitan falsos
#: faltantes sin abrir la puerta a emparejar cosas distintas.
_DATE_TOLERANCE = timedelta(days=3)


def reconcile_erp(
    ledger: Ledger,
    book: Ledger,
    *,
    account_code: str = "",
    date_tolerance: timedelta = _DATE_TOLERANCE,
) -> ErpReport:
    """Concilia un ledger operativo contra su libro contable."""
    report = ErpReport(
        ledger_id=ledger.id, book_ledger_id=book.id, account_code=account_code
    )

    posted = [m for m in book if m.status is MovementStatus.APPROVED]
    unposted = [m for m in book if m.status is not MovementStatus.APPROVED]
    candidatos = [m for m in ledger if m.counts_for_reconciliation]

    # Los tipos sin cuenta en el plan no compiten por líneas del libro. Es un
    # hecho verificado del plan contable (`ERP_UNREPRESENTABLE_KINDS`): un FEE
    # no puede estar en la cuenta puente, así que cualquier línea que
    # coincidiera por monto representaría otro hecho. Dejarlos competir fue un
    # bug latente: una comisión de monto igual a un giro se llevaba la línea
    # del giro en el pase por monto, y el giro real quedaba como faltante.
    sin_cuenta = erp_unrepresentable_kinds(ledger.id)
    representables = [m for m in candidatos if m.kind.value not in sin_cuenta]

    usados: set[str] = set()          # líneas del libro ya asignadas
    resueltos: dict[str, ErpFinding] = {}  # movimiento del ledger -> finding

    # ── Pase 1: por referencia, recorriendo el LIBRO ─────────────────────
    #
    # Se itera el libro y no el ledger a propósito. Una referencia identifica
    # una **transacción**, no un movimiento: en el ledger apunta a hasta cinco
    # movimientos (pago, comisión, IVA, retención, giro) y en el libro a una
    # sola línea. Recorriendo el ledger, la comisión llega primero y se lleva
    # la línea de la venta — que fue exactamente el bug que esto arregla.
    por_referencia = _group_by_reference(representables)
    for line in _reference_index(posted):
        ref = _normalize(line.reference)
        grupo = [m for m in por_referencia.get(ref, []) if m.id not in resueltos]
        if not grupo:
            continue
        elegido = _closest_amount(grupo, line)
        usados.add(line.id)
        resueltos[elegido.id] = _compare(elegido, line, RULE_BY_REFERENCE, ref)

    # ── Pase 2: por monto y fecha, para lo que quedó ─────────────────────
    by_amount: dict[int, list[Movement]] = defaultdict(list)
    for m in posted:
        if m.id not in usados:
            by_amount[m.amount.amount].append(m)

    for movement in candidatos:
        if movement.id in resueltos:
            continue
        if movement.kind.value in sin_cuenta:
            resueltos[movement.id] = _unrepresentable(movement)
            continue
        resueltos[movement.id] = _match_by_amount(
            movement, by_amount, usados, date_tolerance
        )

    report.findings.extend(resueltos[m.id] for m in candidatos)
    report.findings.extend(_orphan_book_lines(posted, usados))
    report.findings.extend(_not_posted(unposted))
    report.findings.sort(key=lambda f: (f.occurred_on or date.min, f.status.value))
    return report


# ── índices ─────────────────────────────────────────────────────────────────


def _normalize(reference: str | None) -> str | None:
    """Las referencias del ERP vienen en mayúsculas y las del canal en minúsculas
    (`TKFGJOKOQFHWVIGU71QQQ` ↔ `tkfgjokoqfhwvigu71qqq`)."""
    return reference.strip().upper() if reference and reference.strip() else None


def _reference_index(book: list[Movement]) -> list[Movement]:
    """Líneas del libro cuya referencia identifica una sola línea.

    Una referencia repetida no es llave: emparejar por ella asignaría cualquiera
    de las repetidas. Se excluyen **contando**, no con una lista negra — así, si
    el contador cambia el texto de `"Acreditación Wompi"`, la regla sigue
    valiendo sin tocar código.
    """
    conteo = Counter(ref for m in book if (ref := _normalize(m.reference)) is not None)
    return [
        m
        for m in book
        if (ref := _normalize(m.reference)) is not None and conteo[ref] == 1
    ]


def _group_by_reference(movements: list[Movement]) -> dict[str, list[Movement]]:
    grupos: dict[str, list[Movement]] = defaultdict(list)
    for m in movements:
        if (ref := _normalize(m.reference)) is not None:
            grupos[ref].append(m)
    return grupos


def _closest_amount(grupo: list[Movement], line: Movement) -> Movement:
    """De los movimientos que comparten referencia, el que corresponde a la línea.

    El de monto idéntico si existe; si no, el más cercano. Que la diferencia
    quede grande no se esconde: `_compare` la reporta como `AMOUNT_MISMATCH`,
    que es lo que hay que ver cuando el ERP registró el hecho con otro número.
    """
    return min(grupo, key=lambda m: (abs((m.amount - line.amount).amount), m.id))


# ── matching ────────────────────────────────────────────────────────────────


def _match_by_amount(
    movement: Movement,
    by_amount: dict[int, list[Movement]],
    usados: set[str],
    date_tolerance: timedelta,
) -> ErpFinding:
    candidatos = [
        m
        for m in by_amount.get(movement.amount.amount, [])
        if m.id not in usados
        and abs(m.occurred_on - movement.occurred_on) <= date_tolerance
    ]
    if candidatos:
        elegido = min(
            candidatos,
            key=lambda m: (abs(m.occurred_on - movement.occurred_on), m.id),
        )
        usados.add(elegido.id)
        return _compare(movement, elegido, RULE_BY_AMOUNT_DATE, None, date_tolerance)

    return _missing_in_erp(movement)


def _compare(
    movement: Movement,
    line: Movement,
    rule: str,
    matched_ref: str | None,
    date_tolerance: timedelta | None = None,
) -> ErpFinding:
    builder = ExplanationBuilder(rule, movement.amount.currency)
    coincide = movement.amount == line.amount
    asiento = line.metadata.get("move_name")

    if matched_ref:
        como = f"la referencia «{matched_ref}», que identifica una sola línea del libro"
        confianza = Confidence.EXACT if coincide else Confidence.HIGH
    else:
        dias = abs((line.occurred_on - movement.occurred_on).days)
        como = (
            f"el monto exacto y una diferencia de {dias} día(s) en la fecha; "
            f"la referencia del libro no identifica una sola línea, así que no "
            f"sirve de llave"
        )
        confianza = Confidence.HIGH if coincide and dias == 0 else Confidence.MEDIUM

    if coincide:
        return ErpFinding(
            status=ErpStatus.MATCHED,
            explanation=builder.build(
                summary=(
                    f"El movimiento de {movement.amount} del {movement.occurred_on} "
                    f"está registrado en el asiento {asiento} por el mismo monto. "
                    f"Se emparejaron por {como}."
                ),
                source_ids=(movement.id,),
                target_ids=(line.id,),
                gross=movement.amount,
                net=line.amount,
                window=(
                    TimeWindow(
                        start=movement.occurred_on - date_tolerance,
                        end=movement.occurred_on + date_tolerance,
                        rule=f"±{date_tolerance.days} días",
                    )
                    if date_tolerance
                    else None
                ),
                confidence=confianza,
            ),
            ledger_movement_id=movement.id,
            book_movement_id=line.id,
            erp_line_id=line.external_id,
            erp_move_name=str(asiento) if asiento else None,
            occurred_on=movement.occurred_on,
            ledger_amount=movement.amount,
            book_amount=line.amount,
            kind=movement.kind.value,
        )

    diferencia = line.amount - movement.amount
    return ErpFinding(
        status=ErpStatus.AMOUNT_MISMATCH,
        explanation=ExplanationBuilder(RULE_AMOUNT_MISMATCH, movement.amount.currency).build(
            summary=(
                f"El asiento {asiento} registra {line.amount} y el movimiento real "
                f"fue {movement.amount}: una diferencia de {diferencia}. "
                f"Se emparejaron por {como}, así que representan el mismo hecho "
                f"registrado con otro número."
            ),
            source_ids=(movement.id,),
            target_ids=(line.id,),
            gross=movement.amount,
            net=line.amount,
            confidence=Confidence.HIGH,
            unexplained=diferencia,
        ),
        ledger_movement_id=movement.id,
        book_movement_id=line.id,
        erp_line_id=line.external_id,
        erp_move_name=str(asiento) if asiento else None,
        occurred_on=movement.occurred_on,
        ledger_amount=movement.amount,
        book_amount=line.amount,
        kind=movement.kind.value,
    )


def _unrepresentable(movement: Movement) -> ErpFinding:
    """Un tipo sin cuenta en el plan. Falta del libro y **no puede estar**.

    No se busca coincidencia a propósito, y la explicación lo dice: afirmar que
    una línea de la cuenta puente ES una comisión contradiría el hecho —mirado
    en Odoo, no derivado de contar ceros— de que las comisiones no tienen
    cuenta donde asentarse. El estado sigue siendo `MISSING_IN_ERP` porque eso
    es verdad; la regla distinta es lo que permite separar *"falta asentar"* de
    *"falta la cuenta"* sin inventar un estado nuevo.
    """
    return ErpFinding(
        status=ErpStatus.MISSING_IN_ERP,
        explanation=ExplanationBuilder(
            RULE_NO_ACCOUNT_IN_CHART, movement.amount.currency
        ).build(
            summary=(
                f"El movimiento de {movement.amount} del {movement.occurred_on} "
                f"({movement.kind.value}) no está en el libro contable y no puede "
                f"estarlo: el plan de cuentas no tiene una cuenta donde asentar "
                f"este tipo. No se buscó una línea equivalente — cualquier "
                f"coincidencia por monto en esta cuenta representaría otro hecho. "
                f"Se resuelve rediseñando el plan de cuentas, no registrando."
            ),
            source_ids=(movement.id,),
            gross=movement.amount,
            confidence=Confidence.HIGH,
            unexplained=movement.amount,
        ),
        ledger_movement_id=movement.id,
        occurred_on=movement.occurred_on,
        ledger_amount=movement.amount,
        kind=movement.kind.value,
    )


def _missing_in_erp(movement: Movement) -> ErpFinding:
    return ErpFinding(
        status=ErpStatus.MISSING_IN_ERP,
        explanation=ExplanationBuilder(
            RULE_MISSING_IN_ERP, movement.amount.currency
        ).build(
            summary=(
                f"El movimiento de {movement.amount} del {movement.occurred_on} "
                f"({movement.kind.value}) ocurrió pero no está registrado en el "
                f"libro contable: no hay línea con esa referencia ni con ese monto "
                f"en una fecha cercana."
            ),
            source_ids=(movement.id,),
            gross=movement.amount,
            confidence=Confidence.HIGH,
            unexplained=movement.amount,
        ),
        ledger_movement_id=movement.id,
        occurred_on=movement.occurred_on,
        ledger_amount=movement.amount,
        kind=movement.kind.value,
    )


def _orphan_book_lines(book: list[Movement], usados: set[str]) -> list[ErpFinding]:
    """Líneas del libro que ningún movimiento respalda.

    Es la dirección inversa y hay que reportarla: un ERP que registra algo que
    no pasó es tan problema como uno al que le falta un registro.
    """
    salida: list[ErpFinding] = []
    for line in book:
        if line.id in usados:
            continue
        asiento = line.metadata.get("move_name")
        salida.append(
            ErpFinding(
                status=ErpStatus.MISSING_IN_LEDGER,
                explanation=ExplanationBuilder(
                    RULE_MISSING_IN_LEDGER, line.amount.currency
                ).build(
                    summary=(
                        f"El asiento {asiento} registra {line.amount} el "
                        f"{line.occurred_on} (diario {line.metadata.get('journal')}), "
                        f"pero ningún movimiento observado lo respalda."
                    ),
                    target_ids=(line.id,),
                    net=line.amount,
                    confidence=Confidence.MEDIUM,
                    unexplained=line.amount,
                ),
                book_movement_id=line.id,
                erp_line_id=line.external_id,
                erp_move_name=str(asiento) if asiento else None,
                occurred_on=line.occurred_on,
                book_amount=line.amount,
                kind="asiento",
            )
        )
    return salida


def _not_posted(lines: list[Movement]) -> list[ErpFinding]:
    """Asientos en borrador o anulados.

    No forman parte del libro formal, así que no se comparan — pero tampoco se
    silencian: un asiento en borrador es trabajo a medio hacer que alguien tiene
    que confirmar o descartar.
    """
    salida: list[ErpFinding] = []
    for line in lines:
        estado = line.metadata.get("state", "?")
        asiento = line.metadata.get("move_name")
        salida.append(
            ErpFinding(
                status=ErpStatus.NOT_POSTED,
                explanation=ExplanationBuilder(
                    RULE_NOT_POSTED, line.amount.currency
                ).build(
                    summary=(
                        f"El asiento {asiento} por {line.amount} del "
                        f"{line.occurred_on} está en estado «{estado}», así que no "
                        f"forma parte del libro formal. No se compara contra el "
                        f"ledger: hay que confirmarlo o descartarlo."
                    ),
                    target_ids=(line.id,),
                    net=line.amount,
                    confidence=Confidence.HIGH,
                ),
                book_movement_id=line.id,
                erp_line_id=line.external_id,
                erp_move_name=str(asiento) if asiento else None,
                occurred_on=line.occurred_on,
                book_amount=line.amount,
                kind="asiento",
            )
        )
    return salida
