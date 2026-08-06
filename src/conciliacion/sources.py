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
from .ingest.connectors.local_file import LocalFileConnector
from .ingest.connectors.odoo_rpc import OdooRpcConnector
from .ingest.connectors.wompi_api import WompiApiConnector
from .ingest.registry import SourceRegistry, SourceSpec
from .settings import Settings


def build_registry(settings: Settings, *, offline: bool = False) -> SourceRegistry:
    """Arma el registro con todas las fuentes conocidas.

    `offline=True` omite las fuentes que requieren red. Sirve para reprocesar
    desde lo ya archivado en `data/raw/` sin credenciales ni conexión — que es
    como debería poder correr quien clona el repositorio.
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

    if offline:
        return registry

    # ── Wompi: API REST ───────────────────────────────────────────────────
    archive = raw / "wompi" / "api"
    registry.register_source(
        SourceSpec(
            name="wompi_api_transacciones",
            connector=WompiApiConnector("transactions", settings.wompi, archive_dir=archive),
            adapters=(WompiApiTransactionsAdapter(),),
        )
    )
    registry.register_source(
        SourceSpec(
            name="wompi_api_desembolsos",
            connector=WompiApiConnector("disbursements", settings.wompi, archive_dir=archive),
            adapters=(WompiApiDisbursementsAdapter(),),
        )
    )

    # ── Odoo: el libro contable de cada ledger ────────────────────────────
    #
    # Se define por CUENTA, no por diario: las líneas de 1110001 aparecen en
    # tres diarios distintos y tomar el diario perdería los giros al banco.
    odoo_archive = raw / "odoo"
    for base_id, account_code in ODOO_LEDGER_ACCOUNTS.items():
        registry.register_source(
            SourceSpec(
                name=f"odoo_libro_{base_id}",
                connector=OdooRpcConnector(
                    account_code, settings.odoo, archive_dir=odoo_archive
                ),
                adapters=(OdooLedgerAdapter(erp_ledger_id(base_id), account_code),),
            )
        )

    return registry
