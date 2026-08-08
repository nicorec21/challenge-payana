"""Adapter del CSV de desembolsos de Wompi.

Layout (columnas exactas del reporte estándar "diario desembolso"):

    id de la transaccion, fecha, referencia, monto, moneda, medio de pago,
    comisión, iva comisión, reteica, reteiva, retefuente, impoconsumo,
    total desembolsado, documento del pagador, tipo de documento del pagador

Es la **única** fuente con el desglose fiscal. La API da el bruto por
transacción y el neto por desembolso, pero no cómo se reparte la diferencia
entre comisión, IVA y las tres retenciones — y ese reparto es exactamente lo
que la Fase 3 necesita para armar el asiento en Odoo (530505 / 240810 / 236500).

Por eso este adapter emite `FEE` y `TAX`, y **no** emite el pago: el `monto`
que también trae la fila se usa para validar contra la API, no para ingerir.
La redundancia entre fuentes se convierte en chequeo en vez de en duplicado.

Dos cosas del layout que no son obvias:

- **La fecha de desembolso está en el nombre del archivo, no adentro.**
  `14-04-2026-disbursement-report-rs-203607-<token>.csv`. Por eso el adapter
  necesita el nombre y no solo las filas, y por eso `RawRecord` lleva
  `metadata` además del payload.

- **El ID de transacción trae un epoch UTC embebido**
  (`1203607-`**`1776117137`**`-25637`). Reconstruye la columna `fecha` exacto
  interpretando UTC−5, así que sirve de checksum del parseo y desambigua
  `DD-MM` de `MM-DD`.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from ...config import TARIFF_DEVIATION_TOLERANCE, FeeSchedule, fee_schedule_for
from ...domain.money import Money
from ...domain.movement import Movement, MovementKind, MovementStatus
from ..ports import IngestionError, RawRecord

_COT = timezone(timedelta(hours=-5))

_HEADERS = (
    "id de la transaccion", "fecha", "referencia", "monto", "moneda",
    "medio de pago", "comisión", "iva comisión", "reteica", "reteiva",
    "retefuente", "impoconsumo", "total desembolsado",
)

#: Fecha de desembolso al principio del nombre del archivo.
_FILENAME_DATE = re.compile(r"^(?P<d>\d{2})-(?P<m>\d{2})-(?P<y>\d{4})")

#: Columnas de descuento → cómo se modela cada una.
#: Se separan `FEE` (lo que cobra Wompi) de `TAX` (lo que el Estado retiene)
#: porque van a cuentas contables distintas en Odoo y porque responden
#: preguntas distintas: la comisión es un costo negociable, la retención no.
_DEDUCTIONS: tuple[tuple[str, MovementKind, str], ...] = (
    ("comisión", MovementKind.FEE, "Comisión Wompi"),
    ("iva comisión", MovementKind.TAX, "IVA sobre comisión"),
    ("retefuente", MovementKind.TAX, "Retención en la fuente"),
    ("reteica", MovementKind.TAX, "ReteICA"),
    ("reteiva", MovementKind.TAX, "ReteIVA"),
    ("impoconsumo", MovementKind.TAX, "Impuesto al consumo"),
)


#: Columnas que el tarifario sabe predecir, y con qué fórmula.
#:
#: Las otras tres (`reteica`, `reteiva`, `impoconsumo`) no tienen tasa modelada:
#: no aparecen con valor distinto de cero en ninguna fila de la muestra, así que
#: inventarles una fórmula sería afirmar algo que nadie verificó. Se ingieren y
#: se declaran no verificables, que es distinto de darlas por buenas.
_VERIFIABLE: tuple[tuple[str, Callable[[FeeSchedule, Money], Money]], ...] = (
    ("comisión", FeeSchedule.commission),
    ("iva comisión", FeeSchedule.iva),
    ("retefuente", FeeSchedule.retefuente),
)

#: Firma de `config.fee_schedule_for`. Se inyecta para poder auditar contra un
#: tarifario de prueba sin tocar el vigente.
ScheduleLookup = Callable[[str, date], "FeeSchedule | None"]


class DisbursementCsvIntegrityError(IngestionError):
    """Una fila no cumple la identidad bruto − descuentos = neto."""


@dataclass(frozen=True, slots=True)
class _Row:
    transaction_id: str
    occurred_at: datetime
    reference: str
    gross: Money
    payment_method: str
    deductions: dict[str, Money]
    net: Money

    @property
    def total_deductions(self) -> Money:
        return Money.sum(self.deductions.values(), self.gross.currency)


class WompiDisbursementCsvAdapter:
    """CSV de desembolsos → movimientos `FEE` y `TAX`."""

    source_id = "wompi_disbursement_csv"
    ledger_id = "wompi"

    def __init__(self, schedule_lookup: ScheduleLookup = fee_schedule_for) -> None:
        self._schedule_for = schedule_lookup

    def sniff(self, record: RawRecord) -> bool:
        if not isinstance(record.payload, (bytes, bytearray)):
            return False
        head = bytes(record.payload[:400]).decode("utf-8-sig", errors="ignore").lower()
        return "id de la transaccion" in head and "total desembolsado" in head

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        settled_on = _settlement_date(record)
        for row in _rows(record):
            _verify(row, record.locator)
            for column, kind, label in _DEDUCTIONS:
                amount = row.deductions[column]
                if amount.is_zero:
                    continue  # no se ingiere un movimiento de cero
                yield Movement(
                    ledger_id=self.ledger_id,
                    source_id=self.source_id,
                    # Sufijo por columna: una transacción produce hasta 6
                    # movimientos distintos y cada uno necesita identidad propia.
                    external_id=f"{row.transaction_id}:{column}",
                    occurred_on=row.occurred_at.date(),
                    occurred_at=row.occurred_at,
                    amount=-amount,
                    kind=kind,
                    status=MovementStatus.APPROVED,
                    description=label,
                    reference=row.reference,
                    metadata={
                        "transaction_id": row.transaction_id,
                        "deduction": column,
                        "payment_method_type": row.payment_method,
                        # Bruto y neto declarados por esta fuente. No se
                        # ingieren como movimiento (los aportan los adapters de
                        # la API); quedan acá para poder validar el cruce.
                        "declared_gross": str(row.gross.units),
                        "declared_net": str(row.net.units),
                        "settled_on": settled_on.isoformat(),
                    },
                    raw_ref=f"{record.locator}#tx={row.transaction_id}",
                )

    def audit(self, record: RawRecord) -> Iterator[str]:
        """Contrasta cada descuento declarado contra el tarifario vigente.

        El CSV es la fuente de verdad y se ingiere tal cual: si Wompi sube la
        comisión, el número nuevo entra sin que haya que tocar nada. El problema
        es el otro: **entra sin que nadie se entere**. La fila sigue cumpliendo
        `bruto − descuentos = neto`, concilia perfecto, y el sistema no tiene
        cómo notar que la tasa cambió.

        Importa porque los descuentos se leen en una minoría de los desembolsos
        (4 CSV de 56 días). El resto se **infiere** con `WOMPI_FEES`, que seguiría
        usando la tasa vieja y produciría residuos sin causa visible. Este aviso
        es lo que conecta el síntoma con el motivo.

        Se agrupa por columna en vez de emitir una línea por fila: que **todas**
        las filas se desvíen igual es la firma de un cambio de tarifa, y una fila
        sola la de una tarifa negociada o un error de carga. Un aviso por fila
        haría que las dos se lean igual.
        """
        desvios: dict[str, list[tuple[str, Money, Money]]] = {}
        sin_tarifario: dict[str, int] = {}
        filas = 0

        for row in _rows(record):
            filas += 1
            schedule = self._schedule_for(row.payment_method, row.occurred_at.date())
            if schedule is None:
                medio = row.payment_method or "(vacío)"
                sin_tarifario[medio] = sin_tarifario.get(medio, 0) + 1
                continue
            for column, formula in _VERIFIABLE:
                declarado = row.deductions[column]
                esperado = formula(schedule, row.gross)
                if abs((declarado - esperado).amount) > TARIFF_DEVIATION_TOLERANCE.amount:
                    desvios.setdefault(column, []).append(
                        (row.transaction_id, declarado, esperado)
                    )

        for medio, n in sorted(sin_tarifario.items()):
            yield (
                f"medio de pago '{medio}' sin tarifario ({n} de {filas} filas): "
                f"los descuentos se ingieren declarados pero no se pueden verificar, "
                f"y los desembolsos sin CSV de ese medio no se van a poder inferir"
            )

        for column, casos in sorted(desvios.items()):
            tx, declarado, esperado = casos[0]
            alcance = (
                "TODAS las filas del archivo" if len(casos) == filas
                else f"{len(casos)} de {filas} filas"
            )
            pista = (
                "así se ve un cambio de tarifa: actualizá WOMPI_FEES en config.py "
                "cerrando la vigente con effective_to y agregando la nueva"
                if len(casos) == filas
                else "una sola fila desviada suele ser tarifa negociada o error de carga"
            )
            yield (
                f"'{column}' no coincide con el tarifario en {alcance} — "
                f"declarado {declarado}, tarifario {esperado} "
                f"(dif {declarado - esperado}, ej. tx {tx}). {pista}"
            )


# ── parseo ──────────────────────────────────────────────────────────────────


def _settlement_date(record: RawRecord) -> date:
    """La fecha de desembolso sale del nombre del archivo.

    Verificado contra el extracto bancario: esta fecha coincide **exacto** con
    el día del crédito en Bancolombia en 10 de 10 casos de abril 2026.
    """
    filename = str(record.metadata.get("filename") or record.locator.rsplit("/", 1)[-1])
    if not (m := _FILENAME_DATE.match(filename)):
        raise IngestionError(
            f"El nombre '{filename}' no empieza con la fecha de desembolso (DD-MM-YYYY)",
            record.locator,
        )
    return date(int(m["y"]), int(m["m"]), int(m["d"]))


def _rows(record: RawRecord) -> Iterator[_Row]:
    text = bytes(record.payload).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    missing = [h for h in _HEADERS if h not in (reader.fieldnames or [])]
    if missing:
        raise IngestionError(f"Faltan columnas: {missing}", record.locator)

    for line_no, raw in enumerate(reader, start=2):
        tx_id = (raw.get("id de la transaccion") or "").strip()
        if not tx_id:
            continue  # línea en blanco al final del archivo
        try:
            currency = (raw.get("moneda") or "COP").strip() or "COP"
            yield _Row(
                transaction_id=tx_id,
                occurred_at=_parse_datetime(raw["fecha"], tx_id, record.locator),
                reference=(raw.get("referencia") or "").strip(),
                gross=Money.parse(raw["monto"], currency),
                payment_method=(raw.get("medio de pago") or "").strip(),
                deductions={
                    column: Money.parse(raw[column] or "0", currency)
                    for column, _, _ in _DEDUCTIONS
                },
                net=Money.parse(raw["total desembolsado"], currency),
            )
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(
                f"Fila {line_no} ilegible: {exc}", record.locator, exc
            ) from exc


def _parse_datetime(raw: str, transaction_id: str, locator: str) -> datetime:
    """`29-04-2026 21:22` en hora de Colombia, validado contra el epoch del ID.

    El ID de transacción tiene forma `<comercio>-<epoch>-<seq>`. Reconstruir la
    fecha desde ese epoch y compararla contra la columna es un checksum gratis
    del parseo, y además desambigua `DD-MM` de `MM-DD` en los días ≤ 12.
    """
    declared = datetime.strptime(raw.strip(), "%d-%m-%Y %H:%M").replace(tzinfo=_COT)

    parts = transaction_id.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        from_epoch = datetime.fromtimestamp(int(parts[1]), tz=UTC).astimezone(_COT)
        if from_epoch.replace(second=0, microsecond=0) != declared:
            raise IngestionError(
                f"La fecha '{raw.strip()}' no coincide con el epoch del ID "
                f"({from_epoch:%d-%m-%Y %H:%M}) — parseo o dato corrupto",
                locator,
            )
    return declared


def _verify(row: _Row, locator: str) -> None:
    """Identidad que toda fila cumple: bruto − descuentos = neto.

    Verificada al centavo en las 9 filas de la muestra. Una fila que no la
    cumple está mal parseada o el dato está corrupto; en cualquier caso, sus
    comisiones no se pueden usar para explicar nada.
    """
    esperado = row.gross - row.total_deductions
    if esperado != row.net:
        raise DisbursementCsvIntegrityError(
            f"La transacción {row.transaction_id} no cierra: "
            f"{row.gross} − {row.total_deductions} = {esperado}, "
            f"pero el CSV declara {row.net}",
            locator,
        )
