"""Punto de entrada del sistema.

Deliberadamente delgado. No es la interfaz de usuario —para eso está la web—:
es lo que dispara el pipeline y regenera las salidas del repositorio. Toda la
lógica vive en el dominio y en los motores; acá solo hay orquestación y
formateo.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import typer

from .config import ACCOUNTS, erp_accounts
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

LEDGERS = tuple(a.id for a in ACCOUNTS) + tuple(a.id for a in erp_accounts())


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
        # Sin recorte: un aviso de tarifario es raro y accionable. Cortarlo a
        # los primeros N escondería justo el que dice qué cambió.
        for locator, aviso in report.warnings:
            typer.secho(f"      ⚠ {locator}: {aviso}", fg="yellow")

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
def reconcile(
    canal: str = typer.Argument("wompi", help="Ledger del canal."),
    banco: str = typer.Argument("bancolombia", help="Ledger bancario."),
    desde: str | None = typer.Option(None, help="Acota la cobertura (YYYY-MM-DD)."),
    hasta: str | None = typer.Option(None, help="Acota la cobertura (YYYY-MM-DD)."),
    salida: str | None = typer.Option(
        None, "--salida", help="Directorio donde escribir el reporte. Default: data/out/."
    ),
) -> None:
    """Concilia el flujo canal → banco y escribe las dos salidas.

    Genera el reporte legible para el CFO y el JSON estructurado desde el mismo
    resultado: si se generaran por caminos distintos, podrían afirmar cosas
    distintas sobre el mismo hecho.
    """
    import json

    from .reconcile.flow.engine import reconcile_flow
    from .report.cfo import render_flow_report
    from .report.contract import to_dict
    from .report.flow_views import build_flow_report

    settings = load_settings()
    with SqliteRepository(settings.database_path) as repo:
        try:
            canal_led = repo.load_ledger(canal)
            banco_led = repo.load_ledger(banco)
        except KeyError as exc:
            typer.secho(f"{exc}. Corré primero: conciliacion ingest <ledger>", fg="red")
            raise typer.Exit(1) from None

        cobertura = _coverage(canal_led, banco_led, desde, hasta)
        report = reconcile_flow(canal_led, banco_led, coverage=cobertura)
        view = build_flow_report(report)
        run_id = repo.save_flow_run(view)

    destino = Path(salida) if salida else settings.out_dir
    destino.mkdir(parents=True, exist_ok=True)
    md = destino / f"conciliacion-flujo-{canal}-{banco}.md"
    js = destino / f"conciliacion-flujo-{canal}-{banco}.json"
    md.write_text(render_flow_report(report), encoding="utf-8")
    js.write_text(
        json.dumps(to_dict(view), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    typer.secho(f"\n{canal} → {banco}   (corrida {run_id})", bold=True)
    for estado, n in sorted(report.counts().items()):
        color = "red" if estado in ("unmatched_settlement", "unmatched_bank") else None
        typer.secho(f"  {estado:<24} {n:>4}", fg=color)
    typer.echo(f"\n  conciliado    {report.matched_amount()}")
    typer.echo(f"  sin explicar  {report.unexplained_total()}")
    typer.secho(f"  a revisar     {len(report.problems)}", bold=True)
    typer.echo(f"\n  CFO   {md}\n  IA    {js}")


def _coverage(canal_led, banco_led, desde: str | None, hasta: str | None):
    """Cobertura efectiva de cada fuente.

    Se puede acotar a mano porque el rango de movimientos observados es una
    aproximación conservadora: un mes sin movimientos es indistinguible de un
    mes que nadie bajó. Ver `reconcile_flow`.
    """
    from .reconcile.flow.findings import Coverage

    def recortar(rango):
        if rango is None:
            return None
        inicio = max(rango[0], date.fromisoformat(desde)) if desde else rango[0]
        fin = min(rango[1], date.fromisoformat(hasta)) if hasta else rango[1]
        return (inicio, fin) if inicio <= fin else None

    return Coverage(
        channel=recortar(canal_led.date_range), bank=recortar(banco_led.date_range)
    )


@app.command(name="reconcile-erp")
def reconcile_erp_cmd(
    ledger: str = typer.Argument("wompi", help="Ledger a comparar contra su libro."),
    salida: str | None = typer.Option(
        None, "--salida", help="Directorio de salida. Default: data/out/."
    ),
) -> None:
    """Concilia un ledger contra su libro contable en Odoo.

    Pregunta contable, distinta de la de flujo: no infiere de qué canal vino la
    plata, compara cada ledger contra su libro formal línea por línea.
    """
    import json

    from .config import ODOO_LEDGER_ACCOUNTS, erp_ledger_id
    from .reconcile.erp.engine import reconcile_erp
    from .report.contract import to_dict
    from .report.erp_cfo import render_erp_report
    from .report.erp_views import build_erp_report

    account_code = ODOO_LEDGER_ACCOUNTS.get(ledger)
    if account_code is None:
        typer.secho(
            f"'{ledger}' no tiene libro contable declarado. "
            f"Opciones: {', '.join(ODOO_LEDGER_ACCOUNTS)}",
            fg="red",
        )
        raise typer.Exit(1)

    settings = load_settings()
    with SqliteRepository(settings.database_path) as repo:
        try:
            operativo = repo.load_ledger(ledger)
            libro = repo.load_ledger(erp_ledger_id(ledger))
        except KeyError as exc:
            typer.secho(
                f"{exc}. Corré primero: conciliacion ingest {ledger} && "
                f"conciliacion ingest {erp_ledger_id(ledger)}",
                fg="red",
            )
            raise typer.Exit(1) from None

        report = reconcile_erp(operativo, libro, account_code=account_code)
        view = build_erp_report(report)

    destino = Path(salida) if salida else settings.out_dir
    destino.mkdir(parents=True, exist_ok=True)
    md = destino / f"conciliacion-erp-{ledger}.md"
    js = destino / f"conciliacion-erp-{ledger}.json"
    md.write_text(render_erp_report(report), encoding="utf-8")
    js.write_text(json.dumps(to_dict(view), ensure_ascii=False, indent=2), encoding="utf-8")

    typer.secho(f"\n{ledger} vs libro Odoo (cuenta {account_code})", bold=True)
    for estado, n in sorted(report.counts().items()):
        color = "red" if estado in ("amount_mismatch", "missing_in_ledger") else None
        typer.secho(f"  {estado:<20} {n:>4}", fg=color)
    typer.echo(f"\n  cobertura del ERP  {report.coverage_ratio():.1%}")
    typer.echo(f"  monto conciliado   {report.matched_amount()}")
    typer.secho(f"  a revisar          {len(report.problems)}", bold=True)
    typer.echo(f"\n  CFO   {md}\n  IA    {js}")


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
