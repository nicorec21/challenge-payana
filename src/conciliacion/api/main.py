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

from ..config import ACCOUNTS
from ..domain.ledger import Ledger
from ..domain.movement import MovementKind
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
            raise HTTPException(
                404,
                f"El ledger '{ledger_id}' no tiene datos. "
                f"Corré: conciliacion ingest {ledger_id}",
            ) from None


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
        for account in ACCOUNTS:
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
