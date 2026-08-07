"""Composición de una corrida: cargar los ledgers y correr el motor.

Existe para que **todos los consumidores pidan la misma conciliación por el
mismo camino**: la web, el CLI y las herramientas del agente. Sin esto cada uno
arma su propia invocación, y con el tiempo divergen en un default —una ventana,
una cobertura, una tolerancia— y el sistema afirma dos cosas distintas sobre el
mismo hecho.

Es el mismo motivo por el que la explicación es un objeto y no un string
(ADR-0004), aplicado un nivel más arriba: no alcanza con proyectar igual si se
calcula distinto.

Este módulo **no decide** nada de negocio ni formatea nada: carga, invoca, y
deja que el que llama traduzca los errores a lo suyo (un 404, un mensaje de
consola, un error de herramienta).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from ..config import ODOO_LEDGER_ACCOUNTS, erp_ledger_id
from ..domain.ledger import Ledger
from .erp.findings import ErpReport
from .flow.findings import Coverage, FlowReport


class SinDatos(LookupError):
    """El ledger no tiene movimientos ingeridos. Lleva el remedio adentro."""

    def __init__(self, ledger_id: str) -> None:
        self.ledger_id = ledger_id
        super().__init__(
            f"El ledger '{ledger_id}' no tiene datos. "
            f"Corré: conciliacion ingest {ledger_id}"
        )


class SinLibroContable(LookupError):
    """El ledger no tiene una cuenta declarada en el plan de Odoo."""

    def __init__(self, ledger_id: str) -> None:
        self.ledger_id = ledger_id
        self.opciones = sorted(ODOO_LEDGER_ACCOUNTS)
        super().__init__(
            f"'{ledger_id}' no tiene libro contable declarado. "
            f"Opciones: {', '.join(self.opciones)}"
        )


class Repositorio(Protocol):
    """Lo único que este módulo necesita de la persistencia."""

    def load_ledger(
        self, ledger_id: str, *, start: date | None = ..., end: date | None = ...
    ) -> Ledger: ...


def load(repo: Repositorio, ledger_id: str) -> Ledger:
    try:
        return repo.load_ledger(ledger_id)
    except KeyError:
        raise SinDatos(ledger_id) from None


def flow(
    repo: Repositorio,
    canal: str = "wompi",
    banco: str = "bancolombia",
    *,
    desde: date | None = None,
    hasta: date | None = None,
) -> FlowReport:
    """Conciliación de flujo canal → banco.

    Se calcula al vuelo. El volumen lo permite —56 giros contra 426 movimientos
    bancarios— y evita servir un resultado viejo después de reingerir.
    """
    from .flow.engine import reconcile_flow

    canal_led, banco_led = load(repo, canal), load(repo, banco)
    return reconcile_flow(
        canal_led, banco_led, coverage=coverage(canal_led, banco_led, desde, hasta)
    )


def erp(repo: Repositorio, ledger_id: str) -> ErpReport:
    """Conciliación de un ledger contra su libro contable en Odoo."""
    from .erp.engine import reconcile_erp

    account_code = ODOO_LEDGER_ACCOUNTS.get(ledger_id)
    if account_code is None:
        raise SinLibroContable(ledger_id)
    return reconcile_erp(
        load(repo, ledger_id),
        load(repo, erp_ledger_id(ledger_id)),
        account_code=account_code,
    )


def coverage(
    canal: Ledger, banco: Ledger, desde: date | None, hasta: date | None
) -> Coverage:
    """Cobertura efectiva de cada fuente, opcionalmente acotada a mano.

    Se puede acotar porque el rango de movimientos observados es una
    aproximación **conservadora**: un mes sin movimientos es indistinguible de
    un mes que nadie bajó. Es lo que separa «falta plata» de «falta data», así
    que el default no puede ser silencioso. Ver `reconcile_flow`.
    """

    def recortar(rango: tuple[date, date] | None) -> tuple[date, date] | None:
        if rango is None:
            return None
        inicio = max(rango[0], desde) if desde else rango[0]
        fin = min(rango[1], hasta) if hasta else rango[1]
        return (inicio, fin) if inicio <= fin else None

    return Coverage(channel=recortar(canal.date_range), bank=recortar(banco.date_range))
