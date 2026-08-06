"""Reporte del ERP en Markdown para el CFO.

Responde una pregunta contable: **¿mi system of record refleja lo que pasó?**

Criterio de redacción, distinto al del reporte de flujo:

1. **El titular es la cobertura**, no la lista de diferencias. Que el ERP
   registre el 24% de los movimientos es más importante que cualquier caso
   individual.
2. **Los faltantes se agrupan por tipo.** «El ERP no registra ninguna comisión»
   es una conclusión; 27 líneas de comisión suelta son ruido con la misma
   información.
3. **Las coincidencias llevan los dos identificadores.** Es lo que el enunciado
   pide, y es lo que permite ir a Odoo y abrir el asiento.
"""

from __future__ import annotations

from ..domain.money import Money
from ..reconcile.erp.findings import ErpFinding, ErpReport, ErpStatus

_TITULOS = {
    ErpStatus.AMOUNT_MISMATCH: "Registrado con otro monto",
    ErpStatus.MISSING_IN_LEDGER: "El ERP registra algo que no ocurrió",
    ErpStatus.NOT_POSTED: "Asientos sin confirmar",
}


def render_erp_report(report: ErpReport) -> str:
    partes = [
        _encabezado(report),
        _veredicto(report),
        _diferencias_de_monto(report),
        _faltantes_en_el_erp(report),
        _otros_problemas(report),
        _coincidencias(report),
        _nota(report),
    ]
    return "\n".join(p for p in partes if p).rstrip() + "\n"


def _encabezado(r: ErpReport) -> str:
    return (
        f"# Conciliación contra el ERP — {r.ledger_id}\n\n"
        f"**Libro contable:** cuenta `{r.account_code}` en Odoo\n"
    )


def _veredicto(r: ErpReport) -> str:
    cobertura = r.coverage_ratio()
    conciliados = len(r.of(ErpStatus.MATCHED))
    del_ledger = sum(1 for f in r.findings if f.ledger_movement_id)

    if cobertura >= 0.99:
        titulo = "## ✅ El ERP refleja lo que pasó"
    elif cobertura >= 0.5:
        titulo = f"## ⚠️ El ERP registra el {cobertura:.0%} de los movimientos"
    else:
        titulo = f"## 🔴 El ERP registra solo el {cobertura:.0%} de los movimientos"

    cuerpo = (
        f"De **{del_ledger}** movimientos que ocurrieron, el libro contable "
        f"registra **{conciliados}** por {r.matched_amount()}. "
    )

    faltan = len(r.of(ErpStatus.MISSING_IN_ERP))
    if faltan:
        cuerpo += f"Faltan **{faltan}** por registrar. "

    sobran = len(r.of(ErpStatus.MISSING_IN_LEDGER))
    if sobran:
        cuerpo += (
            f"Y hay **{sobran}** asiento(s) que el libro registra sin que ningún "
            f"movimiento observado los respalde. "
        )

    difieren = len(r.of(ErpStatus.AMOUNT_MISMATCH))
    if difieren:
        cuerpo += f"**{difieren}** están registrados con un monto distinto al real."

    return f"{titulo}\n\n{cuerpo.strip()}\n"


def _diferencias_de_monto(r: ErpReport) -> str:
    """Primero, porque es lo más grave: el hecho está registrado, mal."""
    grupo = r.of(ErpStatus.AMOUNT_MISMATCH)
    if not grupo:
        return ""
    filas = "\n".join(
        f"| {f.occurred_on} | {f.erp_move_name or '—'} | {f.ledger_amount} | "
        f"{f.book_amount} | {f.difference} |"
        for f in grupo
    )
    return (
        "## Registrado con otro monto\n\n"
        "El ERP tiene el asiento pero por un importe distinto al que realmente "
        "ocurrió. Es el caso más grave: los estados contables ya salieron con ese "
        "número.\n\n"
        "| Fecha | Asiento | Real | En el ERP | Diferencia |\n"
        "|---|---|---:|---:|---:|\n" + filas + "\n"
    )


def _faltantes_en_el_erp(r: ErpReport) -> str:
    grupos = r.by_kind(ErpStatus.MISSING_IN_ERP)
    if not grupos:
        return ""
    filas = "\n".join(
        f"| {_ETIQUETA.get(kind, kind)} | {info['count']} | {info['total']} |"
        for kind, info in grupos.items()
    )
    total = Money.sum(
        f.ledger_amount for f in r.of(ErpStatus.MISSING_IN_ERP) if f.ledger_amount
    )
    return (
        "## Movimientos que el ERP no registra\n\n"
        "Agrupados por tipo: importa más *qué clase* de hecho no se está "
        "contabilizando que la lista de casos.\n\n"
        "| Tipo | Cantidad | Monto |\n|---|---:|---:|\n" + filas + "\n\n"
        f"Neto sin registrar: **{total}**.\n"
    )


def _otros_problemas(r: ErpReport) -> str:
    partes: list[str] = []
    for status in (ErpStatus.MISSING_IN_LEDGER, ErpStatus.NOT_POSTED):
        grupo = r.of(status)
        if not grupo:
            continue
        partes.append(f"## {_TITULOS[status]}\n")
        if status is ErpStatus.NOT_POSTED:
            partes.append(
                "Existen en Odoo pero en borrador o anulados, así que no forman "
                "parte del libro formal. Hay que confirmarlos o descartarlos.\n"
            )
        else:
            partes.append(
                "Un ERP que registra algo que no pasó es tan problema como uno "
                "al que le falta un registro.\n"
            )
        filas = "\n".join(
            f"| {f.occurred_on} | {f.erp_move_name or '—'} | {f.book_amount} |"
            for f in grupo
        )
        partes.append("| Fecha | Asiento | Monto |\n|---|---|---:|\n" + filas + "\n")
    return "\n".join(partes)


def _coincidencias(r: ErpReport) -> str:
    grupo = r.of(ErpStatus.MATCHED)
    if not grupo:
        return ""
    filas = "\n".join(_fila_coincidencia(f) for f in grupo)
    return (
        f"## Coincidencias ({len(grupo)})\n\n"
        "Cada fila lleva el identificador del movimiento y el del asiento: "
        "representan la misma cosa y se pueden abrir en ambos sistemas.\n\n"
        "| Fecha | Monto | Movimiento | Asiento | Línea Odoo | Emparejado por |\n"
        "|---|---:|---|---|---:|---|\n" + filas + "\n"
    )


def _fila_coincidencia(f: ErpFinding) -> str:
    como = "referencia" if "reference" in f.explanation.rule_id else "monto y fecha"
    return (
        f"| {f.occurred_on} | {f.ledger_amount} | `{f.ledger_movement_id}` | "
        f"{f.erp_move_name or '—'} | {f.erp_line_id or '—'} | {como} |"
    )


def _nota(r: ErpReport) -> str:
    return (
        "---\n\n"
        "## Cómo leer esto\n\n"
        f"**Qué es el libro.** Todas las líneas contables de la cuenta "
        f"`{r.account_code}`, vengan del diario que vengan. No alcanza con mirar "
        "un diario: las líneas de esta cuenta aparecen en varios, y tomar uno solo "
        "dejaría afuera parte de la historia.\n\n"
        "**Cómo se emparejan.** Por la referencia del asiento cuando esa "
        "referencia identifica una sola línea; si el mismo texto se repite en "
        "varias, por monto y fecha con unos días de margen —el asiento puede "
        "llevar la fecha del hecho o la de registración—.\n\n"
        "**Qué no se compara.** Los asientos en borrador o anulados no forman "
        "parte del libro formal, y las ventas rechazadas no deberían estar en él.\n"
    )


_ETIQUETA = {
    "payment": "Ventas cobradas",
    "fee": "Comisiones",
    "tax": "IVA y retenciones",
    "settlement": "Giros al banco",
    "bank_credit": "Créditos bancarios",
    "bank_debit": "Débitos bancarios",
    "asiento": "Asientos",
}
