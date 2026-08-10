"""Composition root: acá se declaran las fuentes del sistema.

Es el único lugar que conoce simultáneamente cuentas, connectors y adapters.
Ni el dominio ni el pipeline saben que existe Wompi.

**Este archivo es la medida de la extensibilidad.** Sumar una fuente nueva
—el POS bancario, UberEats, otro banco— debería tocar solamente esto y un
adapter. Si además hubiera que modificar el motor de conciliación, el diseño
falló. Ver ADR-0002.
"""

from __future__ import annotations

from .config import ACCOUNTS, ODOO_LEDGER_ACCOUNTS, erp_accounts, erp_ledger_id
from .ingest.adapters.bancolombia_pdf import BancolombiaPdfAdapter
from .ingest.adapters.odoo_ledger import OdooLedgerAdapter
from .ingest.adapters.wompi_api import (
    WompiApiDisbursementsAdapter,
    WompiApiTransactionsAdapter,
)
from .ingest.adapters.wompi_disbursement_csv import WompiDisbursementCsvAdapter
from .ingest.connectors.archive_replay import ArchiveReplayConnector
from .ingest.connectors.local_file import LocalFileConnector
from .ingest.connectors.odoo_rpc import OdooRpcConnector
from .ingest.connectors.wompi_api import WompiApiConnector
from .ingest.registry import SourceRegistry, SourceSpec
from .settings import Settings


def build_registry(settings: Settings, *, offline: bool = False) -> SourceRegistry:
    """Arma el registro con todas las fuentes conocidas.

    `offline=True` reemplaza los connectors de red por un replay de lo ya
    archivado en `data/raw/`: mismas fuentes, mismos adapters, cero
    credenciales. Quien clona el repositorio reconstruye todos los ledgers
    —y la conciliación completa— desde lo versionado.
    """
    registry = SourceRegistry()
    for account in ACCOUNTS:
        registry.register_account(account)
    for account in erp_accounts():
        registry.register_account(account)

    raw = settings.raw_data_dir

    # ── Bancolombia: extractos PDF ────────────────────────────────────────
    registry.register_source(
        SourceSpec(
            name="bancolombia_extractos_pdf",
            connector=LocalFileConnector(
                raw / "bancolombia", "*.pdf", connector_id="local_pdf"
            ),
            adapters=(BancolombiaPdfAdapter(),),
        )
    )

    # ── Wompi: CSV de desembolsos (única fuente del desglose fiscal) ──────
    registry.register_source(
        SourceSpec(
            name="wompi_desembolsos_csv",
            connector=LocalFileConnector(
                raw / "wompi", "*.csv", connector_id="local_csv"
            ),
            adapters=(WompiDisbursementCsvAdapter(),),
        )
    )

    # ── Wompi: API REST ───────────────────────────────────────────────────
    #
    # En vivo el connector archiva cada página ya redactada en `data/raw/`;
    # offline, un replay de ese archivo reemite los mismos registros con los
    # mismos locators. Misma fuente, mismos adapters: cambia solo el transporte,
    # y los ledgers que producen ambos caminos son idénticos movimiento a
    # movimiento.
    archive = raw / "wompi" / "api"
    for resource, name, adapter in (
        ("transactions", "wompi_api_transacciones", WompiApiTransactionsAdapter()),
        ("disbursements", "wompi_api_desembolsos", WompiApiDisbursementsAdapter()),
    ):
        connector = (
            ArchiveReplayConnector(
                archive / resource,
                fragment="id",
                metadata={"resource": resource, "redacted": True},
                connector_id=f"replay_wompi_{resource}",
            )
            if offline
            else WompiApiConnector(resource, settings.wompi, archive_dir=archive)
        )
        registry.register_source(
            SourceSpec(name=name, connector=connector, adapters=(adapter,))
        )

    # ── Odoo: el libro contable de cada ledger ────────────────────────────
    #
    # Se define por CUENTA, no por diario: las líneas de 1110001 aparecen en
    # tres diarios distintos y tomar el diario perdería los giros al banco.
    odoo_archive = raw / "odoo"
    for base_id, account_code in ODOO_LEDGER_ACCOUNTS.items():
        connector = (
            ArchiveReplayConnector(
                odoo_archive / account_code,
                fragment="line",
                metadata={"account_code": account_code, "redacted": True},
                connector_id=f"replay_odoo_{account_code}",
            )
            if offline
            else OdooRpcConnector(account_code, settings.odoo, archive_dir=odoo_archive)
        )
        registry.register_source(
            SourceSpec(
                name=f"odoo_libro_{base_id}",
                connector=connector,
                adapters=(OdooLedgerAdapter(erp_ledger_id(base_id), account_code),),
            )
        )

    return registry
