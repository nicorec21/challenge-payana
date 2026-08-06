"""Reporte en Markdown para el CFO.

Es una **proyección** del mismo `FlowReport` que produce el JSON: no calcula ni
decide nada por su cuenta. Si este renderer hiciera cuentas, la consola y la web
podrían afirmar cosas distintas sobre el mismo hecho (ADR-0004).

Criterio de redacción, que es donde está el trabajo:

1. **Lo primero es la respuesta, no el método.** El CFO quiere saber si falta
   plata; el detalle de cómo se concluyó va después.
2. **Los problemas arriba, y separados de las notas al pie.** Un giro sin
   crédito bancario y un giro fuera del período de datos se ven igual en una
   tabla y significan lo contrario.
3. **Nada de "no concilia" sin decir cuánto y por qué.** Un número sin
   explicación obliga a abrir el sistema, que es justo lo que este reporte
   tiene que evitar.
"""

from __future__ import annotations

from ..domain.explanation import Confidence, EvidenceSource
from ..domain.money import Money
from ..reconcile.flow.findings import FlowFinding, FlowReport, FlowStatus

_TITULOS = {
    FlowStatus.UNMATCHED_SETTLEMENT: "Plata que salió del canal y no apareció en el banco",
    FlowStatus.UNMATCHED_BANK: "Plata que entró al banco sin origen identificado",
    FlowStatus.AMBIGUOUS: "Casos con más de una interpretación posible",
}

_CONFIANZA = {
    Confidence.EXACT: "exacta",
    Confidence.HIGH: "alta",
    Confidence.MEDIUM: "media",
    Confidence.LOW: "baja",
}


def render_flow_report(report: FlowReport) -> str:
    partes = [
        _encabezado(report),
        _veredicto(report),
        _problemas(report),
        _fuera_de_alcance(report),
        _conciliado(report),
        _nota_metodologica(report),
    ]
    return "\n".join(p for p in partes if p).rstrip() + "\n"


# ── secciones ───────────────────────────────────────────────────────────────


def _encabezado(r: FlowReport) -> str:
    cov = r.coverage.overlap
    periodo = f"{cov[0]} a {cov[1]}" if cov else "sin período en común entre las fuentes"
    return (
        f"# Conciliación de flujo — {r.channel_ledger_id} → {r.bank_ledger_id}\n\n"
        f"**Período analizado:** {periodo}\n"
    )


def _veredicto(r: FlowReport) -> str:
    """La respuesta antes que el método."""
    conciliados = len(r.of(FlowStatus.MATCHED))
    problemas = r.problems

    # El monto en disputa es el de los casos problemáticos, NO el total sin
    # explicar: ese incluye los centavos de redondeo de las conciliaciones que
    # sí cerraron, y mezclarlos daría un número que no coincide con la suma del
    # detalle de abajo.
    en_disputa = Money.sum(
        f.explanation.unexplained for f in problemas if f.explanation.unexplained
    )
    redondeo = r.unexplained_total() - en_disputa

    if not problemas:
        titulo = "## ✅ Todo el dinero del período está explicado"
        cuerpo = (
            f"Se conciliaron **{conciliados}** giros por **{r.matched_amount()}**. "
            f"No hay plata que haya salido del canal sin aparecer en el banco, "
            f"ni créditos bancarios del canal sin origen identificado."
        )
    else:
        titulo = f"## ⚠️ Hay {len(problemas)} caso(s) que requieren revisión"
        cuerpo = (
            f"Se conciliaron **{conciliados}** giros por **{r.matched_amount()}**. "
            f"Quedan **{len(problemas)}** casos sin explicar, por un total de "
            f"**{en_disputa}**. El detalle está abajo."
        )

    fuera = len(r.of(FlowStatus.OUT_OF_COVERAGE))
    if fuera:
        cuerpo += (
            f"\n\nAdemás hay **{fuera}** movimiento(s) sobre los que el sistema no "
            f"puede opinar porque caen fuera del período que cubren los datos. "
            f"**No son faltantes de plata**, son faltantes de información."
        )

    if not redondeo.is_zero:
        cuerpo += (
            f"\n\nEn las conciliaciones que sí cerraron quedan **{redondeo}** de "
            f"diferencia acumulada por redondeo, repartidos entre los casos donde "
            f"hubo que estimar las comisiones. Es un centavo por venta como máximo "
            f"y no afecta ninguna conclusión."
        )
    return f"{titulo}\n\n{cuerpo}\n"


def _problemas(r: FlowReport) -> str:
    if not r.problems:
        return ""
    partes = ["## Casos que requieren revisión\n"]
    for status in (
        FlowStatus.UNMATCHED_SETTLEMENT,
        FlowStatus.UNMATCHED_BANK,
        FlowStatus.AMBIGUOUS,
    ):
        grupo = r.of(status)
        if not grupo:
            continue
        total = Money.sum(
            f.settlement_amount or f.bank_amount for f in grupo if f.settlement_amount or f.bank_amount
        )
        partes.append(f"### {_TITULOS[status]}\n")
        partes.append(f"{len(grupo)} caso(s), {total} en total.\n")
        for f in grupo:
            partes.append(_detalle(f))
    return "\n".join(partes)


def _fuera_de_alcance(r: FlowReport) -> str:
    grupo = r.of(FlowStatus.OUT_OF_COVERAGE)
    if not grupo:
        return ""
    filas = "\n".join(
        f"| {f.occurred_on} | {f.settlement_amount or f.bank_amount} | {f.explanation.summary} |"
        for f in grupo
    )
    return (
        "## Fuera del alcance de los datos\n\n"
        "El sistema **no afirma que falte plata** en estos casos: afirma que no "
        "tiene con qué compararlos.\n\n"
        "| Fecha | Monto | Motivo |\n|---|---:|---|\n" + filas + "\n"
    )


def _conciliado(r: FlowReport) -> str:
    grupo = r.of(FlowStatus.MATCHED)
    if not grupo:
        return ""
    filas = "\n".join(
        f"| {f.occurred_on} | {f.bank_amount} | {len(f.transaction_ids)} | "
        f"{_origen_ajustes(f)} | {_CONFIANZA[f.confidence]} |"
        for f in grupo
    )
    return (
        f"## Conciliado ({len(grupo)})\n\n"
        "| Fecha | Acreditado | Ventas | Comisiones | Confianza |\n"
        "|---|---:|---:|---|---|\n" + filas + "\n"
    )


def _detalle(f: FlowFinding) -> str:
    """Un caso problemático, con todo lo necesario para actuar."""
    e = f.explanation
    lineas = [
        f"**{f.occurred_on} — {f.settlement_amount or f.bank_amount}**\n",
        f"{e.summary}\n",
    ]
    if e.gross and e.adjustments:
        lineas.append(f"- Bruto cobrado: {e.gross}")
        for a in e.adjustments:
            marca = "" if a.source is EvidenceSource.DECLARED else " *(estimado)*"
            lineas.append(f"- {a.kind.value.capitalize()}: −{a.amount}{marca}")
    if e.unexplained:
        lineas.append(f"- **Sin explicar: {e.unexplained}**")
    if e.alternatives:
        lineas.append("")
        lineas.append("Alternativas que el sistema descartó:")
        for alt in e.alternatives:
            lineas.append(f"- {alt.description} — {alt.rejected_because}")
    lineas.append("")
    return "\n".join(lineas)


def _nota_metodologica(r: FlowReport) -> str:
    """Cómo leer el reporte. Va al final: es el método, no la respuesta."""
    declarados = sum(
        1 for f in r.findings
        if any(a.source is EvidenceSource.DECLARED for a in f.explanation.adjustments)
    )
    inferidos = sum(
        1 for f in r.findings
        if any(a.source is EvidenceSource.INFERRED for a in f.explanation.adjustments)
    )
    return (
        "---\n\n"
        "## Cómo leer esto\n\n"
        "**Agrupamiento de ventas.** No se adivina: cada venta declara a qué giro "
        "pertenece, así que no hay ambigüedad sobre qué compone cada acreditación.\n\n"
        "**Comisiones.** En "
        f"{declarados} caso(s) el canal declaró el desglose exacto. En {inferidos} "
        "hubo que estimarlo con el tarifario vigente; esa estimación tiene un margen "
        "conocido de un centavo por venta, y por eso esos casos figuran con confianza "
        "alta en vez de exacta.\n\n"
        "**Confianza.** *Exacta* significa que cada peso está respaldado por un dato "
        "declarado por el canal. *Alta*, que el monto coincide pero parte del desglose "
        "se estimó. *Media* o *baja* significan que conviene mirar el caso.\n\n"
        "**Fechas.** El banco puede acreditar el mismo día del giro o hasta tres días "
        "hábiles después; el sistema considera esa ventana y descuenta fines de semana "
        "y festivos colombianos.\n"
    )


def _origen_ajustes(f: FlowFinding) -> str:
    fuentes = {a.source for a in f.explanation.adjustments}
    if not fuentes:
        return "—"
    if fuentes == {EvidenceSource.DECLARED}:
        return "declaradas"
    if EvidenceSource.DECLARED in fuentes:
        return "mixtas"
    return "estimadas"
