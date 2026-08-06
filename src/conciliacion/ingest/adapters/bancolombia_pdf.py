"""Adapter del extracto de Bancolombia en PDF.

Layout observado (Ene–Abr 2026, cuenta de ahorros):

    ESTADO DE CUENTA                        BANCOLOMBIA
    ALIMENTOS ALCAZAR SAS  CUENTA DE AHORROS  NÚMERO 19300008472
    DESDE: 2026/03/31 HASTA: 2026/04/30

    SALDO ANTERIOR $ 284,557,304.49    SALDO PROMEDIO ...
    TOTAL ABONOS   $ 124,816,595.77    ...
    TOTAL CARGOS   $ 212,819,125.66    ...
    SALDO ACTUAL   $ 196,554,774.60    ...

    FECHA  DESCRIPCIÓN                  VALOR         SALDO
    1/04   PAGO DE PROV WOMPI S.A.S.  1,327,369.53  285,884,674.02

Tres decisiones que vale la pena entender antes de tocar este archivo:

1. **Se extrae por coordenadas, no por layout de texto.** `pdftotext -layout`
   desalinea las columnas VALOR/SALDO respecto de FECHA/DESCRIPCIÓN en este
   PDF: los valores salen asociados a la fila equivocada. Agrupar palabras por
   su centro vertical reconstruye las filas correctamente. Verificado sobre 426
   líneas de los 4 extractos.

2. **La unidad atómica es el archivo, no la línea.** Una línea suelta no es
   interpretable: no trae el año (está en el encabezado) y no se puede validar
   sin la cadena de saldos completa. Por eso el `RawRecord` es el PDF entero.

3. **El extracto se autovalida.** Trae tres invariantes redundantes (ver
   `_verify`). Si el parseo pierde o malinterpreta una línea, se rompen. Un
   extracto que no cierra se rechaza entero: conciliar contra un extracto
   incompleto produce faltantes falsos, que es peor que no conciliar.
"""

from __future__ import annotations

import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterator

import pdfplumber

from ...domain.money import Money
from ...domain.movement import Movement, MovementKind, MovementStatus
from ..ports import IngestionError, RawRecord

#: Fila de movimiento: día/mes, descripción, valor con signo, saldo acumulado.
#: La descripción es no-greedy para que los dos montos finales ganen; funciona
#: incluso con descripciones que contienen números ("PAGO PSE DIAN EDIFICIO 9211")
#: porque esos no llevan decimales y no matchean el patrón de monto.
_ROW = re.compile(
    r"^(?P<dia>\d{1,2})/(?P<mes>\d{2})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<valor>-?[\d,]+\.\d{2})\s+"
    r"(?P<saldo>-?[\d,]+\.\d{2})$"
)

_PERIODO = re.compile(r"DESDE:\s*(?P<desde>[\d/]+)\s+HASTA:\s*(?P<hasta>[\d/]+)")
_CUENTA = re.compile(r"N[ÚU]MERO\s+(?P<cuenta>\d+)")
_RESUMEN = re.compile(r"^(?P<label>SALDO ANTERIOR|TOTAL ABONOS|TOTAL CARGOS|SALDO ACTUAL)\s+\$\s*(?P<valor>-?[\d,]+\.\d{2})")

#: Tolerancia vertical para agrupar palabras en una fila, en puntos PDF. Las
#: filas de este layout están separadas ~18pt, así que 2pt no puede fusionar
#: dos filas distintas.
_ROW_TOLERANCE = 2


class StatementIntegrityError(IngestionError):
    """El extracto parseado no cumple sus propios invariantes."""


@dataclass(frozen=True, slots=True)
class StatementHeader:
    account_number: str
    desde: date
    hasta: date

    @property
    def period_id(self) -> str:
        """Identifica el extracto. Entra en la clave de idempotencia."""
        return f"{self.hasta:%Y-%m}"


@dataclass(frozen=True, slots=True)
class StatementSummary:
    saldo_anterior: Money
    total_abonos: Money
    total_cargos: Money
    saldo_actual: Money


class BancolombiaPdfAdapter:
    """Traduce un extracto PDF de Bancolombia a movimientos canónicos.

    No clasifica: una línea que dice WOMPI se ingiere como un crédito bancario
    cualquiera. Decidir que ese crédito *es* una liquidación de Wompi es
    trabajo del motor de conciliación, y tiene que poder explicarlo. Si el
    adapter lo etiquetara, esa conclusión entraría al sistema sin evidencia.
    """

    source_id = "bancolombia_pdf"
    ledger_id = "bancolombia"

    def sniff(self, record: RawRecord) -> bool:
        """Reconoce el layout por el encabezado.

        Se lee solo la primera página: barato, y alcanza para descartar."""
        if not isinstance(record.payload, (bytes, bytearray)):
            return False
        if not record.payload[:5] == b"%PDF-":
            return False
        try:
            with pdfplumber.open(io.BytesIO(record.payload)) as pdf:
                text = (pdf.pages[0].extract_text() or "").upper()
        except Exception:
            return False
        return "ESTADO DE CUENTA" in text and "DESDE:" in text and "HASTA:" in text

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        lines = list(_extract_lines(record))
        header = _parse_header(lines, record.locator)
        summary = _parse_summary(lines, record.locator)
        rows = _parse_rows(lines, header)

        _verify(header, summary, rows, record.locator)

        for row in rows:
            yield self._to_movement(record, header, row)

    def _to_movement(
        self, record: RawRecord, header: StatementHeader, row: _Row
    ) -> Movement:
        return Movement(
            ledger_id=self.ledger_id,
            source_id=self.source_id,
            external_id=_synthetic_id(header, row),
            occurred_on=row.day,
            amount=row.valor,
            kind=MovementKind.BANK_CREDIT if row.valor.amount > 0 else MovementKind.BANK_DEBIT,
            status=MovementStatus.APPROVED,
            description=row.descripcion,
            counterparty=None,
            metadata={
                "saldo": str(row.saldo.units),
                "cuenta": header.account_number,
                "periodo": header.period_id,
                "pagina": row.page,
            },
            raw_ref=f"{record.locator}#pagina={row.page},y={row.y}",
        )


# ── parseo ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Line:
    page: int
    y: int
    text: str


@dataclass(frozen=True, slots=True)
class _Row:
    page: int
    y: int
    day: date
    descripcion: str
    valor: Money
    saldo: Money


def _extract_lines(record: RawRecord) -> Iterator[_Line]:
    """Reconstruye filas agrupando palabras por su centro vertical.

    Este es el núcleo del adapter: el PDF no tiene tablas declaradas, las
    columnas son solo posiciones. Ordenar por `x0` dentro de cada banda
    horizontal devuelve la fila en el orden en que se lee.
    """
    try:
        pdf = pdfplumber.open(io.BytesIO(record.payload))
    except Exception as exc:
        raise IngestionError("No se pudo abrir el PDF", record.locator, exc) from exc

    with pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            bands: dict[int, list[dict]] = defaultdict(list)
            for word in page.extract_words():
                center = (word["top"] + word["bottom"]) / 2
                bands[round(center / _ROW_TOLERANCE) * _ROW_TOLERANCE].append(word)
            for y in sorted(bands):
                words = sorted(bands[y], key=lambda w: w["x0"])
                yield _Line(page_number, y, " ".join(w["text"] for w in words))


def _parse_header(lines: list[_Line], locator: str) -> StatementHeader:
    account, periodo = None, None
    for line in lines:
        if account is None and (m := _CUENTA.search(line.text)):
            account = m.group("cuenta")
        if periodo is None and (m := _PERIODO.search(line.text)):
            periodo = (_iso(m.group("desde")), _iso(m.group("hasta")))
        if account and periodo:
            break

    if periodo is None:
        raise IngestionError("No se encontró el período (DESDE/HASTA)", locator)
    if account is None:
        raise IngestionError("No se encontró el número de cuenta", locator)

    return StatementHeader(account_number=account, desde=periodo[0], hasta=periodo[1])


def _parse_summary(lines: list[_Line], locator: str) -> StatementSummary:
    found: dict[str, Money] = {}
    for line in lines:
        if m := _RESUMEN.match(line.text):
            found.setdefault(m.group("label"), Money.parse(m.group("valor")))

    missing = {"SALDO ANTERIOR", "TOTAL ABONOS", "TOTAL CARGOS", "SALDO ACTUAL"} - found.keys()
    if missing:
        raise IngestionError(f"Faltan totales del resumen: {sorted(missing)}", locator)

    return StatementSummary(
        saldo_anterior=found["SALDO ANTERIOR"],
        total_abonos=found["TOTAL ABONOS"],
        total_cargos=found["TOTAL CARGOS"],
        saldo_actual=found["SALDO ACTUAL"],
    )


def _parse_rows(lines: list[_Line], header: StatementHeader) -> list[_Row]:
    rows: list[_Row] = []
    for line in lines:
        if not (m := _ROW.match(line.text)):
            continue
        rows.append(
            _Row(
                page=line.page,
                y=line.y,
                day=_row_date(int(m.group("dia")), int(m.group("mes")), header),
                descripcion=m.group("desc").strip(),
                valor=Money.parse(m.group("valor")),
                saldo=Money.parse(m.group("saldo")),
            )
        )
    return rows


def _row_date(day: int, month: int, header: StatementHeader) -> date:
    """Las filas traen `día/mes` sin año. El año sale del `HASTA`, no del `DESDE`.

    Importa: el extracto de enero dice `DESDE: 2025/12/31 HASTA: 2026/01/31` y
    sus filas son de enero de **2026**. Derivar el año del `DESDE` corre todo
    el extracto un año atrás.

    (El `DESDE` es la fecha del saldo anterior, no de la primera fila. Por eso
    tampoco hay solapamiento de movimientos entre extractos consecutivos.)
    """
    year = header.hasta.year
    if month == 12 and header.hasta.month == 1:
        year -= 1  # extracto de enero con arrastre de diciembre
    return date(year, month, day)


def _iso(raw: str) -> date:
    return date(*(int(part) for part in raw.split("/")))


def _synthetic_id(header: StatementHeader, row: _Row) -> str:
    """Clave de idempotencia. El extracto no trae referencia por línea.

    El **saldo acumulado** es la pieza que la hace única: hay días con 16
    líneas idénticas de `SERVICIO E-MAILS ENVIADOS -280,00`, indistinguibles
    salvo por el saldo, que codifica la posición en la secuencia.

    Verificado sobre las 426 líneas de los 4 extractos:
      (periodo, fecha, desc, valor)         → 18 colisiones
      (periodo, fecha, desc, valor, saldo)  →  0 colisiones

    Se prefiere el saldo a un ordinal de línea porque es **estable ante
    reordenamiento**: si el banco reemite el PDF con las filas en otro orden,
    un ordinal duplicaría el extracto entero; el saldo no.
    """
    material = "\x1f".join(
        [
            header.account_number,
            header.period_id,
            row.day.isoformat(),
            row.descripcion,
            str(row.valor.amount),
            str(row.saldo.amount),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:24]


# ── verificación ────────────────────────────────────────────────────────────


def _verify(
    header: StatementHeader,
    summary: StatementSummary,
    rows: list[_Row],
    locator: str,
) -> None:
    """Tres invariantes redundantes que el propio extracto declara.

    Redundantes a propósito: cada uno detecta un modo de falla distinto.
      1. El resumen cierra consigo mismo → los totales se leyeron bien.
      2. La cadena de saldos no se rompe → no se perdió ni se duplicó una fila.
      3. La suma de filas iguala los totales → no falta un bloque entero.

    Un extracto que no cierra se rechaza completo. Conciliar contra un extracto
    al que le falta una línea produce "faltantes" que no son faltantes de
    plata sino de parseo, y es indistinguible desde el reporte.
    """
    if not rows:
        raise StatementIntegrityError("El extracto no tiene movimientos", locator)

    # 1. saldo_anterior + abonos - cargos == saldo_actual
    esperado = summary.saldo_anterior + summary.total_abonos - summary.total_cargos
    if esperado != summary.saldo_actual:
        raise StatementIntegrityError(
            f"El resumen no cierra: {esperado} != {summary.saldo_actual}", locator
        )

    # 2. cadena de saldos línea a línea
    saldo = summary.saldo_anterior
    for row in rows:
        saldo = saldo + row.valor
        if saldo != row.saldo:
            raise StatementIntegrityError(
                f"Cadena de saldos rota en pág.{row.page} '{row.descripcion}' "
                f"({row.day}): esperaba {saldo}, el extracto dice {row.saldo}",
                locator,
            )
    if saldo != summary.saldo_actual:
        raise StatementIntegrityError(
            f"El saldo final {saldo} no coincide con SALDO ACTUAL {summary.saldo_actual}",
            locator,
        )

    # 3. suma de movimientos vs totales declarados
    abonos = Money.sum((r.valor for r in rows if r.valor.amount > 0))
    cargos = abs(Money.sum((r.valor for r in rows if r.valor.amount < 0)))
    if abonos != summary.total_abonos:
        raise StatementIntegrityError(
            f"TOTAL ABONOS declarado {summary.total_abonos} != sumado {abonos}", locator
        )
    if cargos != summary.total_cargos:
        raise StatementIntegrityError(
            f"TOTAL CARGOS declarado {summary.total_cargos} != sumado {cargos}", locator
        )
