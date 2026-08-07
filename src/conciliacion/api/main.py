"""API HTTP. Delgada a propósito.

Sirve lo que ya está persistido; no ingiere ni concilia. Toda respuesta es una
proyección del contrato en `report/contract.py`, el mismo que consume el CLI.
Si la API calculara algo por su cuenta, la web y la consola podrían afirmar
cosas distintas sobre el mismo hecho.

    uvicorn conciliacion.api.main:app --reload
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from ..config import ACCOUNTS, erp_accounts
from ..domain.ledger import Ledger
from ..domain.movement import MovementKind
from ..reconcile.run import SinDatos
from ..report.contract import (
    CONTRACT_VERSION,
    LedgerSummary,
    MovementView,
    SourceView,
    SystemView,
    now_iso,
    to_dict,
)
from ..report.views import (
    build_disbursement_breakdowns,
    build_statement,
    build_transaction_breakdowns,
    statement_periods,
)
from ..settings import Settings, load_settings
from ..sources import build_registry
from ..storage.sqlite_repo import SqliteRepository

app = FastAPI(
    title="Conciliación — Alimentos Alcázar",
    version=CONTRACT_VERSION,
    description="Lectura de los ledgers normalizados. No ingiere ni concilia.",
)

# El front corre en otro puerto durante el desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return load_settings()


def _repo() -> SqliteRepository:
    return SqliteRepository(_settings().database_path)


def _load(ledger_id: str, start: date | None = None, end: date | None = None) -> Ledger:
    with _repo() as repo:
        try:
            return repo.load_ledger(ledger_id, start=start, end=end)
        except KeyError:
            raise HTTPException(404, str(SinDatos(ledger_id))) from None


def _run(fn, *args, **kwargs):
    """Corre una conciliación traduciendo su error de dominio a un 404.

    El motor no sabe que existe HTTP: informa qué falta y cómo conseguirlo, y
    cada consumidor lo traduce a lo suyo.
    """
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@app.get("/api/system")
def system() -> dict[str, Any]:
    """Vista general: ledgers, fuentes y cobertura."""
    registry = build_registry(_settings(), offline=True)
    con_red = build_registry(_settings(), offline=False) if _tiene_credenciales() else registry

    sources: list[SourceView] = []
    offline_names = {s.name for a in ACCOUNTS for s in registry.sources_for(a.id)}
    for account in con_red.accounts:
        for spec in con_red.sources_for(account.id):
            sources.append(
                SourceView(
                    name=spec.name,
                    ledger_id=spec.ledger_id,
                    connector_id=spec.connector.connector_id,
                    adapters=[a.source_id for a in spec.adapters],
                    requires_network=spec.name not in offline_names,
                )
            )

    ledgers: list[LedgerSummary] = []
    coverage: dict[str, dict[str, str | None]] = {}
    with _repo() as repo:
        for account in (*ACCOUNTS, *erp_accounts()):
            if repo.count(account.id) == 0:
                continue
            ledger = repo.load_ledger(account.id)
            fuentes = [s.name for s in con_red.sources_for(account.id)]
            ledgers.append(LedgerSummary.of(ledger, sources=fuentes))
            rango = repo.date_range(account.id)
            coverage[account.id] = {
                "from": rango[0].isoformat() if rango else None,
                "to": rango[1].isoformat() if rango else None,
            }

    return to_dict(
        SystemView(
            contract_version=CONTRACT_VERSION,
            generated_at=now_iso(),
            ledgers=ledgers,
            sources=sources,
            coverage=coverage,
        )
    )


@app.get("/api/ledgers/{ledger_id}")
def ledger_summary(ledger_id: str) -> dict[str, Any]:
    return to_dict(LedgerSummary.of(_load(ledger_id)))


@app.get("/api/ledgers/{ledger_id}/movements")
def movements(
    ledger_id: str,
    desde: date | None = None,
    hasta: date | None = None,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = Query(None, description="Busca en descripción y referencia."),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    ledger = _load(ledger_id, start=desde, end=hasta)
    items = list(ledger)

    if kind:
        items = [m for m in items if m.kind.value == kind]
    if status:
        items = [m for m in items if m.status.value == status]
    if q:
        needle = q.lower()
        items = [
            m for m in items
            if needle in m.description.lower() or needle in (m.reference or "").lower()
        ]

    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "items": [to_dict(MovementView.of(m)) for m in items[offset : offset + limit]],
    }


@app.get("/api/ledgers/{ledger_id}/movements/{movement_id}")
def movement(ledger_id: str, movement_id: str) -> dict[str, Any]:
    ledger = _load(ledger_id)
    found = ledger.by_id(movement_id)
    if found is None:
        raise HTTPException(404, f"No existe el movimiento {movement_id}")
    return to_dict(MovementView.of(found))


@app.get("/api/ledgers/{ledger_id}/statements")
def statements(ledger_id: str) -> dict[str, Any]:
    """Períodos de extracto disponibles."""
    return {"periods": statement_periods(_load(ledger_id))}


@app.get("/api/ledgers/{ledger_id}/statements/{period}")
def statement(ledger_id: str, period: str) -> dict[str, Any]:
    """Un extracto en orden de documento, con la cadena de saldos verificada.

    `chain_ok` por línea y `chain_intact` global permiten **ver** el invariante
    del extracto en vez de confiar en que la ingesta lo chequeó.
    """
    view = build_statement(_load(ledger_id), period)
    if view.line_count == 0:
        raise HTTPException(404, f"No hay extracto del período {period}")
    return to_dict(view)


@app.get("/api/ledgers/{ledger_id}/transactions")
def transactions(
    ledger_id: str,
    solo_abiertas: bool = Query(False, description="Solo las que no cierran en cero."),
) -> dict[str, Any]:
    """Transacciones del canal con su descomposición.

    Verificación visual del reparto disjunto: el pago viene de la API, los
    descuentos del CSV y la liquidación de otro endpoint, y la suma debe cerrar.
    """
    breakdowns = build_transaction_breakdowns(_load(ledger_id))
    if solo_abiertas:
        breakdowns = [b for b in breakdowns if b.settlement_scope is None]
    return {
        "total": len(breakdowns),
        "con_desglose_declarado": sum(1 for b in breakdowns if b.has_declared_deductions),
        "sin_liquidar": sum(1 for b in breakdowns if b.settlement_scope is None),
        "items": [to_dict(b) for b in breakdowns],
    }


@app.get("/api/ledgers/{ledger_id}/disbursements")
def disbursements(ledger_id: str) -> dict[str, Any]:
    """Desembolsos con las transacciones que los componen.

    Acá se verifica el cierre: `Σ net_expected == |settlement|`. Los que no
    cierran son los que todavía no tienen el desglose de comisiones declarado
    (hay CSV de 4 de 56 días); inferirlos es trabajo de la Fase 2.
    """
    items = build_disbursement_breakdowns(_load(ledger_id))
    return {
        "total": len(items),
        "cierran_en_cero": sum(1 for d in items if d.closes_to_zero),
        "con_desglose_completo": sum(1 for d in items if d.deductions_complete),
        "items": [to_dict(d) for d in items],
    }


@app.get("/api/reconciliation/flow")
def flow(
    canal: str = "wompi",
    banco: str = "bancolombia",
    desde: date | None = None,
    hasta: date | None = None,
) -> dict[str, Any]:
    """Conciliación de flujo canal → banco.

    Se calcula al vuelo sobre los ledgers persistidos. El volumen lo permite
    (56 giros contra 426 movimientos bancarios) y evita servir un resultado
    viejo después de reingerir. Si creciera, se sirve la última corrida
    guardada en vez de recalcular.
    """
    from ..reconcile import run
    from ..report.flow_views import build_flow_report

    with _repo() as repo:
        report = _run(run.flow, repo, canal, banco, desde=desde, hasta=hasta)
    return to_dict(build_flow_report(report))


@app.get("/api/reconciliation/flow/report.md", response_class=PlainTextResponse)
def flow_markdown(canal: str = "wompi", banco: str = "bancolombia") -> str:
    """El mismo resultado, renderizado para el CFO.

    Misma fuente que el JSON: dos proyecciones, un solo cálculo.
    """
    from ..reconcile import run
    from ..report.cfo import render_flow_report

    with _repo() as repo:
        return render_flow_report(_run(run.flow, repo, canal, banco))


@app.get("/api/movements/{movement_id}/trace")
def trace(movement_id: str) -> dict[str, Any]:
    """`Trazar(Movimiento)`: en qué conclusiones participa este movimiento.

    Dado un pago, responde dónde terminaron sus fondos. Lee los findings ya
    persistidos, así que devuelve la última corrida guardada.
    """
    with _repo() as repo:
        findings = repo.findings_touching(movement_id)
    return {"movement_id": movement_id, "findings": findings}


@app.get("/api/reconciliation/erp/{ledger_id}")
def erp(ledger_id: str) -> dict[str, Any]:
    """Conciliación de un ledger contra su libro contable en Odoo.

    Pregunta contable, distinta de la de flujo: no infiere de qué canal vino la
    plata, compara cada ledger contra su libro formal línea por línea.
    """
    from ..reconcile import run
    from ..report.erp_views import build_erp_report

    with _repo() as repo:
        return to_dict(build_erp_report(_run(run.erp, repo, ledger_id)))


@app.get("/api/reconciliation/erp/{ledger_id}/report.md", response_class=PlainTextResponse)
def erp_markdown(ledger_id: str) -> str:
    """El mismo resultado, renderizado para el CFO."""
    from ..reconcile import run
    from ..report.erp_cfo import render_erp_report

    with _repo() as repo:
        return render_erp_report(_run(run.erp, repo, ledger_id))


@app.get("/api/panorama/{source_id}")
def panorama(
    source_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=5, le=200),
    q: str | None = None,
    estado: str | None = Query(None, description="Filtra por estado de conciliación."),
) -> dict[str, Any]:
    """Panorama de una fuente: qué trae, de dónde viene y si está conciliado.

    Paginado del lado del servidor: 426 líneas de extracto en una sola tabla es
    un scroll interminable, y traerlas todas para mostrar 25 desperdicia lo
    mismo del lado del cliente.
    """
    from ..config import ODOO_LEDGER_ACCOUNTS, erp_ledger_id
    from ..reconcile.erp.engine import reconcile_erp
    from ..reconcile.flow.engine import reconcile_flow
    from ..reconcile.flow.findings import Coverage
    from ..report.sources import (
        bancolombia_panorama,
        odoo_panorama,
        paginate,
        wompi_panorama,
    )
    from ..report.views import build_source_groups

    if source_id == "wompi":
        canal, banco = _load("wompi"), _load("bancolombia")
        flujo = reconcile_flow(
            canal, banco,
            coverage=Coverage(channel=canal.date_range, bank=banco.date_range),
        )
        vista = wompi_panorama(canal, build_source_groups(canal), flujo)
    elif source_id == "bancolombia":
        canal, banco = _load("wompi"), _load("bancolombia")
        flujo = reconcile_flow(
            canal, banco,
            coverage=Coverage(channel=canal.date_range, bank=banco.date_range),
        )
        vista = bancolombia_panorama(banco, flujo)
    elif source_id == "odoo":
        libro = _load(erp_ledger_id("wompi"))
        erp = reconcile_erp(
            _load("wompi"), libro, account_code=ODOO_LEDGER_ACCOUNTS["wompi"]
        )
        vista = odoo_panorama(libro, erp)
    else:
        raise HTTPException(404, f"Fuente desconocida: {source_id}")

    items = vista.items
    if estado:
        items = [i for i in items if i.reconciliation == estado]
    if q:
        needle = q.lower()
        items = [
            i for i in items
            if needle in i.label.lower() or needle in (i.sublabel or "").lower()
        ]

    pagina, total_paginas = paginate(items, page, size)
    return {
        "source_id": vista.source_id,
        "name": vista.name,
        "subtitle": vista.subtitle,
        "unit": vista.unit,
        "total": vista.total,
        "counts": vista.counts,
        "filtered": len(items),
        "page": page,
        "size": size,
        "pages": total_paginas,
        "items": to_dict(pagina),
    }


@app.get("/api/sources/{ledger_id}/groups")
def source_groups(ledger_id: str) -> dict[str, Any]:
    """Los datos de una fuente, agrupados como los piensa un operador.

    Para un canal: las ventas dentro del desembolso que las liquidó. El
    agrupamiento usa el `disbursement_id` declarado por el canal, no una
    inferencia.
    """
    from ..report.views import build_source_groups

    grupos = build_source_groups(_load(ledger_id))
    return {
        "ledger_id": ledger_id,
        "group_count": len(grupos),
        "transaction_count": sum(g["transaction_count"] for g in grupos),
        "groups": to_dict(grupos),
    }


@app.get("/api/kinds")
def kinds() -> dict[str, list[str]]:
    from ..domain.movement import MovementStatus

    return {
        "kinds": [k.value for k in MovementKind],
        "statuses": [s.value for s in MovementStatus],
    }


def _tiene_credenciales() -> bool:
    """Si no hay `.env`, la API sigue funcionando: solo deja de listar las
    fuentes de red. El repositorio tiene que poder explorarse recién clonado."""
    try:
        return _settings().wompi is not None
    except RuntimeError:
        return False
