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
        _sin_cuenta_donde_asentarse(report),
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
    # La cobertura va sobre lo **comparable**, no sobre todo. Un porcentaje que
    # baja porque falta un asiento y porque no existe la cuenta donde asentarlo
    # le pide al CFO una acción —registrar— que en el segundo caso no sirve.
    cov = r.coverage()
    cobertura = cov["ratio"]
    conciliados = cov["matched"]

    if cobertura >= 0.99:
        titulo = "## ✅ El ERP refleja lo que pasó"
    elif cobertura >= 0.5:
        titulo = f"## ⚠️ El ERP registra el {cobertura:.0%} de lo que puede registrar"
    else:
        titulo = f"## 🔴 El ERP registra solo el {cobertura:.0%} de lo que puede registrar"

    cuerpo = (
        f"De **{cov['comparable']}** movimientos que ocurrieron y tienen cuenta "
        f"donde asentarse, el libro contable registra **{conciliados}** por "
        f"{r.matched_amount()}. "
    )

    faltan = len(r.of(ErpStatus.MISSING_IN_ERP)) - cov["unrepresentable"]
    if faltan:
        cuerpo += f"Faltan **{faltan}** por registrar. "

    if cov["unrepresentable"]:
        tipos = ", ".join(_ETIQUETA.get(k, k) for k in cov["unrepresentable_kinds"])
        cuerpo += (
            f"Aparte hay **{cov['unrepresentable']}** movimiento(s) "
            f"({tipos.lower()}) por {cov['unrepresentable_total']} que **no tienen "
            f"cuenta en el plan donde asentarse**: el bruto entra a la cuenta "
            f"puente, el neto sale, y la diferencia queda ahí sin llevarse nunca "
            f"a gasto. Eso no se resuelve registrando asientos. "
        )

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
    """Agrupados por tipo, y separando **por qué** falta cada grupo.

    Sin la columna «qué hacer», la tabla le pide al CFO que mande a registrar
    149 asientos, y 27 de esos no se pueden registrar en ningún lado: no existe
    la cuenta. Son dos problemas con dos destinatarios distintos.
    """
    grupos = r.by_kind(ErpStatus.MISSING_IN_ERP)
    if not grupos:
        return ""
    sin_cuenta = set(r.coverage()["unrepresentable_kinds"])

    def fila(kind: str, info: dict) -> str:
        que_hacer = (
            "**no hay cuenta donde asentarlo**"
            if kind in sin_cuenta
            else "registrar el asiento"
        )
        return f"| {_ETIQUETA.get(kind, kind)} | {info['count']} | {info['total']} | {que_hacer} |"

    filas = "\n".join(fila(k, i) for k, i in grupos.items())
    total = Money.sum(
        f.ledger_amount
        for f in r.of(ErpStatus.MISSING_IN_ERP)
        if f.ledger_amount and (f.kind or "") not in sin_cuenta
    )
    nota = (
        "\n\nLas filas marcadas *no hay cuenta donde asentarlo* no son un "
        "descuido del contador: los diarios solo tocan la cuenta puente, ventas "
        "y banco. Corregirlo es una decisión de plan de cuentas, no de "
        "registración — cuáles crear está más abajo.\n"
        if sin_cuenta & set(grupos)
        else "\n"
    )
    return (
        "## Movimientos que el ERP no registra\n\n"
        "Agrupados por tipo: importa más *qué clase* de hecho no se está "
        "contabilizando que la lista de casos.\n\n"
        "| Tipo | Cantidad | Monto | Qué hacer |\n|---|---:|---:|---|\n"
        + filas
        + f"\n\nNeto pendiente de registrar: **{total}**."
        + nota
    )


def _sin_cuenta_donde_asentarse(r: ErpReport) -> str:
    """Cuáles crear. Nada más.

    El veredicto ya dice que faltan cuentas y por qué, y la tabla de faltantes
    ya marca qué filas no se pueden registrar. Lo único que nadie contesta es
    **cuál cuenta abrir**, así que esta sección se limita a eso: repetir el
    mecanismo acá lo diluiría en un párrafo que el lector ya leyó dos veces.

    Va al final del bloque de faltantes porque cambia de destinatario: los
    asientos los hace quien contabiliza, el plan de cuentas lo decide otro.
    """
    from ..config import ODOO_ACCOUNT_REALITY

    cov = r.coverage()
    propuestas = cov["unrepresentable_proposed_accounts"]
    if not cov["unrepresentable"] or not propuestas:
        return ""

    filas = "\n".join(
        f"| {_ETIQUETA.get(kind, kind)} | "
        + ", ".join(f"`{c}`" for c in codigos)
        + " | "
        + "; ".join(
            f"{ODOO_ACCOUNT_REALITY[c][0]} — {ODOO_ACCOUNT_REALITY[c][1]}"
            for c in codigos
            if c in ODOO_ACCOUNT_REALITY
        )
        + " |"
        for kind, codigos in sorted(propuestas.items())
    )
    return (
        "## Qué cuentas habría que abrir\n\n"
        f"Para los **{cov['unrepresentable']}** movimientos de arriba que hoy no "
        "tienen dónde asentarse. Los tres códigos que nombra el enunciado "
        "**existen** en este Odoo, pero con otro nombre y sin uso en los diarios "
        "de Wompi y Bancolombia — así que abrirlos es decidir qué representan, no "
        "solo crearlos:\n\n"
        "| Tipo | Cuenta propuesta | Qué es hoy en Odoo |\n"
        "|---|---|---|\n" + filas + "\n"
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
