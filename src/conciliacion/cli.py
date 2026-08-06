"""Punto de entrada del sistema.

Deliberadamente delgado. No es la interfaz de usuario —para eso está la web—:
es lo que dispara el pipeline y regenera las salidas del repositorio. Toda la
lógica vive en el dominio y en los motores; acá solo hay orquestación y
formateo.
"""

from __future__ import annotations

import sys
from datetime import date

import typer

from .config import ACCOUNTS
from .domain.money import Money
from .ingest.ports import FetchWindow
from .ingest.registry import ingest_all
from .settings import load_settings
from .sources import build_registry
from .storage.sqlite_repo import SqliteRepository


def _forzar_utf8() -> None:
    """La salida lleva acentos, símbolos de moneda y flechas.

    En una consola de Windows con codepage heredado (cp1252), imprimir "→" o
    "—" aborta el proceso con `UnicodeEncodeError`. Sin esto, correr la
    herramienta exigiría configurar la terminal primero — y CI en Ubuntu nunca
    lo detectaría porque ahí el default ya es UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_forzar_utf8()

app = typer.Typer(
    add_completion=False,
    help="Conciliación contable Wompi ↔ Bancolombia ↔ Odoo (Alimentos Alcázar).",
)

LEDGERS = tuple(a.id for a in ACCOUNTS)


@app.command()
def ingest(
    ledger: str = typer.Argument(..., help=f"Ledger a ingerir: {' | '.join(LEDGERS)}"),
    desde: str = typer.Option("2026-01-01", help="Inicio de la ventana (YYYY-MM-DD)."),
    hasta: str = typer.Option("2026-04-30", help="Fin de la ventana (YYYY-MM-DD)."),
    offline: bool = typer.Option(
        False, "--offline", help="Solo fuentes locales; no toca la red."
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Aborta si algún registro no parsea."
    ),
) -> None:
    """Normaliza las fuentes de un ledger y lo persiste."""
    settings = load_settings()
    registry = build_registry(settings, offline=offline)

    if ledger not in LEDGERS:
        typer.secho(f"Ledger desconocido: {ledger}. Opciones: {', '.join(LEDGERS)}", fg="red")
        raise typer.Exit(1)

    window = FetchWindow(start=date.fromisoformat(desde), end=date.fromisoformat(hasta))
    built, reports = ingest_all(registry, ledger, window, strict=strict)

    for report in reports:
        color = "green" if report.ok else "yellow"
        typer.secho(f"  {report.summary()}", fg=color)
        for locator, reason in report.skipped[:5]:
            typer.secho(f"      ! {locator}: {reason}", fg="yellow")

    with SqliteRepository(settings.database_path) as repo:
        nuevos = repo.save_ledger(built)

    typer.secho(
        f"\n{built.id}: {len(built)} movimientos ({nuevos} nuevos en la base)", bold=True
    )
    _print_summary(built)


@app.command()
def show(
    ledger: str = typer.Argument(..., help=f"Ledger a inspeccionar: {' | '.join(LEDGERS)}"),
    desde: str | None = typer.Option(None, help="Filtrar desde (YYYY-MM-DD)."),
    hasta: str | None = typer.Option(None, help="Filtrar hasta (YYYY-MM-DD)."),
) -> None:
    """Muestra el estado de un ledger ya persistido."""
    settings = load_settings()
    with SqliteRepository(settings.database_path) as repo:
        try:
            built = repo.load_ledger(
                ledger,
                start=date.fromisoformat(desde) if desde else None,
                end=date.fromisoformat(hasta) if hasta else None,
            )
        except KeyError:
            typer.secho(f"No hay datos de '{ledger}'. Corré primero: ingest {ledger}", fg="red")
            raise typer.Exit(1) from None

    typer.secho(f"{built.account.name} ({built.id})", bold=True)
    _print_summary(built)


@app.command()
def sources(
    offline: bool = typer.Option(False, "--offline", help="Omitir fuentes de red."),
) -> None:
    """Lista las fuentes registradas y a qué ledger alimentan.

    Sirve para responder de un vistazo cuánto cuesta sumar una fuente nueva.
    """
    registry = build_registry(load_settings(), offline=offline)
    for account in registry.accounts:
        specs = registry.sources_for(account.id)
        typer.secho(f"\n{account.id}  ({account.role})", bold=True)
        for spec in specs:
            adapters = ", ".join(a.source_id for a in spec.adapters)
            typer.echo(f"   {spec.name}")
            typer.echo(f"      connector: {spec.connector.connector_id}")
            typer.echo(f"      adapters:  {adapters}")
        if not specs:
            typer.echo("   (sin fuentes registradas)")


def _print_summary(built) -> None:
    from collections import Counter

    typer.echo(f"  movimientos {len(built)}")
    rango = built.date_range
    if rango:
        typer.echo(f"  período   {rango[0]} → {rango[1]}")
    typer.echo(f"  saldo     {built.balance()}")

    counts = Counter((m.kind.value, m.status.value) for m in built)
    if counts:
        typer.echo("  desglose:")
        for (kind, status), n in sorted(counts.items()):
            total = Money.sum(
                (m.amount for m in built if m.kind.value == kind and m.status.value == status),
                built.currency,
            )
            typer.echo(f"     {kind:<12} {status:<9} {n:>4}   {total}")


if __name__ == "__main__":
    app()
