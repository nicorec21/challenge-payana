"""Adapter del POS bancario — formato Asobancaria 2001 (ancho fijo).

DEMOSTRACIÓN DE EXTENSIBILIDAD. Ver ADR-0009 y la sección del README.

El enunciado pide: *"si mañana AA decide integrar también el POS bancario (otro
formato, otra cadencia de liquidación), ¿cuánto código nuevo hace falta?
Mostralo."*

Este archivo **es** la respuesta. Es código real, testeado, que corre por el
mismo pipeline que Wompi y Bancolombia. Lo sintético son los datos, no el
diseño: `Asobancaria 2001` es un formato bancario colombiano real —el propio
dashboard de Wompi lo ofrece como alternativa al CSV—.

No está registrado en `sources.py`: el POS está fuera del alcance del challenge
y su fixture es sintético. Se registra en su test, que verifica que el pipeline
lo ingiere sin que el motor cambie.

Layout (ancho fijo, decimales implícitos de 2 posiciones):

    CABECERA (tipo 00), 33 chars
      1- 2  tipo = "00"
      3-10  desde AAAAMMDD
     11-18  hasta AAAAMMDD
     19-33  cuenta

    DETALLE (tipo 01), 113 chars
      1- 2  tipo = "01"
      3-10  fecha de la venta AAAAMMDD
     11-16  hora HHMMSS
     17-36  referencia
     37-51  valor bruto      (centavos, sin punto)
     52-66  comisión
     67-81  IVA sobre comisión
     82-96  valor neto
     97-97  signo C/D
     98-105 terminal
    106-113 fecha de liquidación AAAAMMDD

    CONTROL (tipo 99), 40 chars
      1- 2  tipo = "99"
      3-10  cantidad de registros de detalle
     11-25  total bruto
     26-40  total neto
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

from ...domain.money import Money
from ...domain.movement import Movement, MovementKind, MovementStatus
from ..ports import IngestionError, RawRecord

#: (inicio, largo) 0-indexado, sobre los registros de detalle.
_D = {
    "fecha": (2, 8), "hora": (10, 6), "referencia": (16, 20),
    "bruto": (36, 15), "comision": (51, 15), "iva": (66, 15), "neto": (81, 15),
    "signo": (96, 1), "terminal": (97, 8), "liquidacion": (105, 8),
}


class PosFileIntegrityError(IngestionError):
    """El archivo no cumple sus propios totales de control."""


@dataclass(frozen=True, slots=True)
class _Detail:
    sold_on: datetime
    settled_on: date
    reference: str
    terminal: str
    gross: Money
    commission: Money
    iva: Money
    net: Money


class PosAsobancariaAdapter:
    """Archivo de liquidación del POS → movimientos del ledger `pos`.

    Mismo reparto disjunto que Wompi ([ADR-0008](../../docs/adr/0008-reparto-disjunto-entre-fuentes.md)):
    acá una sola fuente trae todo, así que emite los cuatro kinds y el ledger
    cierra en cero por transacción.
    """

    source_id = "pos_asobancaria_2001"
    ledger_id = "pos"

    def sniff(self, record: RawRecord) -> bool:
        if not isinstance(record.payload, (bytes, bytearray)):
            return False
        first = bytes(record.payload[:33]).decode("latin-1", errors="ignore")
        return len(first) == 33 and first.startswith("00") and first[2:18].isdigit()

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        lines = bytes(record.payload).decode("latin-1").splitlines()
        details, control = _read(lines, record.locator)
        _verify(details, control, record.locator)

        for d in details:
            base = {
                "ledger_id": self.ledger_id,
                "source_id": self.source_id,
                "occurred_on": d.sold_on.date(),
                "occurred_at": d.sold_on,
                "status": MovementStatus.APPROVED,
                "reference": d.reference,
                "metadata": {
                    "terminal": d.terminal,
                    "settled_on": d.settled_on.isoformat(),
                    "declared_gross": str(d.gross.units),
                    "declared_net": str(d.net.units),
                },
                "raw_ref": f"{record.locator}#ref={d.reference}",
            }
            yield Movement(external_id=f"{d.reference}:venta", amount=d.gross,
                           kind=MovementKind.PAYMENT, description="Venta POS", **base)
            yield Movement(external_id=f"{d.reference}:comision", amount=-d.commission,
                           kind=MovementKind.FEE, description="Comisión POS", **base)
            yield Movement(external_id=f"{d.reference}:iva", amount=-d.iva,
                           kind=MovementKind.TAX, description="IVA sobre comisión POS", **base)
            yield Movement(
                external_id=f"{d.reference}:liquidacion", amount=-d.net,
                kind=MovementKind.SETTLEMENT, description="Liquidación POS al banco",
                **{**base, "occurred_on": d.settled_on},
            )


def _field(line: str, name: str) -> str:
    start, length = _D[name]
    return line[start:start + length].strip()


def _amount(line: str, name: str) -> Money:
    """Decimales implícitos: el campo ya está en centavos."""
    return Money(int(_field(line, name) or "0"))


def _read(lines: list[str], locator: str) -> tuple[list[_Detail], tuple[int, Money, Money]]:
    details: list[_Detail] = []
    control: tuple[int, Money, Money] | None = None

    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        tipo = line[:2]
        try:
            if tipo == "00":
                continue
            if tipo == "01":
                details.append(
                    _Detail(
                        sold_on=datetime.strptime(
                            _field(line, "fecha") + _field(line, "hora"), "%Y%m%d%H%M%S"
                        ),
                        settled_on=datetime.strptime(_field(line, "liquidacion"), "%Y%m%d").date(),
                        reference=_field(line, "referencia"),
                        terminal=_field(line, "terminal"),
                        gross=_amount(line, "bruto"),
                        commission=_amount(line, "comision"),
                        iva=_amount(line, "iva"),
                        net=_amount(line, "neto"),
                    )
                )
            elif tipo == "99":
                control = (int(line[2:10]), Money(int(line[10:25])), Money(int(line[25:40])))
            else:
                raise ValueError(f"tipo de registro desconocido: {tipo!r}")
        except IngestionError:
            raise
        except Exception as exc:
            raise IngestionError(f"Línea {number} ilegible: {exc}", locator, exc) from exc

    if control is None:
        raise PosFileIntegrityError("Falta el registro de control (tipo 99)", locator)
    return details, control


def _verify(
    details: list[_Detail], control: tuple[int, Money, Money], locator: str
) -> None:
    """Mismo criterio que el extracto bancario (ADR-0007): el archivo trae sus
    propios totales, y si no cierran se rechaza entero."""
    count, total_gross, total_net = control

    if len(details) != count:
        raise PosFileIntegrityError(
            f"El control declara {count} registros y hay {len(details)}", locator
        )
    if (suma := Money.sum(d.gross for d in details)) != total_gross:
        raise PosFileIntegrityError(
            f"Total bruto: control {total_gross}, sumado {suma}", locator
        )
    if (suma := Money.sum(d.net for d in details)) != total_net:
        raise PosFileIntegrityError(
            f"Total neto: control {total_net}, sumado {suma}", locator
        )
    for d in details:
        if d.gross - d.commission - d.iva != d.net:
            raise PosFileIntegrityError(
                f"La venta {d.reference} no cierra: "
                f"{d.gross} − {d.commission} − {d.iva} ≠ {d.net}",
                locator,
            )
