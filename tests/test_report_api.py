"""Contrato de salida, vistas y API.

La API no calcula: proyecta el contrato. Estos tests fijan la forma de esa
proyección, porque es lo que consumen la web y —según el enunciado— una IA
contadora. Un cambio de forma acá rompe consumidores externos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conciliacion.config import BANCOLOMBIA, WOMPI
from conciliacion.domain import Money
from conciliacion.ingest.adapters.bancolombia_pdf import BancolombiaPdfAdapter
from conciliacion.ingest.adapters.pos_asobancaria import PosAsobancariaAdapter
from conciliacion.ingest.adapters.wompi_disbursement_csv import WompiDisbursementCsvAdapter
from conciliacion.ingest.connectors.local_file import LocalFileConnector
from conciliacion.ingest.registry import SourceRegistry, SourceSpec, ingest_all
from conciliacion.report.contract import LedgerSummary, MovementView, to_dict
from conciliacion.report.views import (
    build_disbursement_breakdowns,
    build_statement,
    build_transaction_breakdowns,
    statement_periods,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _ledger(account, directory: str, pattern: str, adapter):
    registry = SourceRegistry()
    registry.register_account(account)
    registry.register_source(
        SourceSpec(
            name=f"{account.id}_src",
            connector=LocalFileConnector(FIXTURES / directory, pattern),
            adapters=(adapter,),
        )
    )
    return ingest_all(registry, account.id, strict=True)[0]


@pytest.fixture
def banco():
    return _ledger(BANCOLOMBIA, "bancolombia", "*.pdf", BancolombiaPdfAdapter())


@pytest.fixture
def wompi_csv():
    """Solo el CSV: sin la API no hay pagos ni liquidaciones."""
    return _ledger(WOMPI, "wompi", "*.csv", WompiDisbursementCsvAdapter())


class TestFormatoDeMontos:
    def test_los_montos_viajan_en_centavos_enteros(self, banco):
        vista = MovementView.of(next(iter(banco)))
        assert isinstance(vista.amount["cents"], int)
        assert set(vista.amount) == {"cents", "currency", "formatted"}

    def test_nunca_hay_floats_en_la_salida(self, banco):
        """Un consumidor que sume floats reintroduce el error de redondeo que
        todo el sistema evita. El contrato no le da la oportunidad."""
        def sin_floats(node):
            if isinstance(node, float):
                raise AssertionError(f"float en el contrato: {node}")
            if isinstance(node, dict):
                for v in node.values():
                    sin_floats(v)
            if isinstance(node, list):
                for v in node:
                    sin_floats(v)

        sin_floats(to_dict(LedgerSummary.of(banco)))
        sin_floats([to_dict(MovementView.of(m)) for m in list(banco)[:50]])

    def test_el_formateo_es_colombiano(self, banco):
        assert Money.parse("1234567.89").format() == "$1.234.567,89 COP"


class TestResumenDeLedger:
    def test_cuenta_y_saldo(self, banco):
        resumen = LedgerSummary.of(banco)
        assert resumen.movement_count == 214
        assert resumen.balance["cents"] == banco.balance().amount
        assert resumen.date_range is not None

    def test_desglose_por_kind_y_status(self, banco):
        resumen = LedgerSummary.of(banco)
        assert {(r.kind, r.status) for r in resumen.breakdown} == {
            ("bank_credit", "approved"),
            ("bank_debit", "approved"),
        }
        assert sum(r.count for r in resumen.breakdown) == 214


class TestExtracto:
    def test_periodos_disponibles(self, banco):
        assert statement_periods(banco) == ["2026-01", "2026-04"]

    def test_orden_de_documento(self, banco):
        """El ledger ordena por (fecha, id); el extracto por posición. Sin esta
        vista, la cadena de saldos no se puede verificar a ojo contra el PDF."""
        vista = build_statement(banco, "2026-04")
        assert [linea.position for linea in vista.lines] == list(range(vista.line_count))

    def test_la_cadena_cierra(self, banco):
        vista = build_statement(banco, "2026-04")
        assert vista.line_count == 110
        assert vista.chain_intact
        assert all(linea.chain_ok for linea in vista.lines)

    def test_apertura_y_cierre_coinciden_con_el_pdf(self, banco):
        """Los totales que declara el RESUMEN del extracto de abril."""
        vista = build_statement(banco, "2026-04")
        assert vista.opening_balance["cents"] == Money.parse("284557304.49").amount
        assert vista.closing_balance["cents"] == Money.parse("196554774.60").amount

    def test_saldo_declarado_y_calculado_van_los_dos(self, banco):
        """Mandar ambos permite VER el invariante, no solo confiar en que la
        ingesta lo verificó."""
        linea = build_statement(banco, "2026-04").lines[0]
        assert linea.running_balance["cents"] == linea.expected_balance["cents"]

    def test_cada_linea_apunta_a_la_evidencia(self, banco):
        vista = build_statement(banco, "2026-04")
        assert all("#pagina=" in linea.raw_ref for linea in vista.lines)

    def test_periodo_inexistente_da_vista_vacia(self, banco):
        assert build_statement(banco, "2030-01").line_count == 0


class TestDescomposicionDeTransacciones:
    def test_solo_el_csv_no_alcanza_para_el_bruto(self, wompi_csv):
        """El CSV emite FEE y TAX; el bruto lo aporta la API (ADR-0008)."""
        items = build_transaction_breakdowns(wompi_csv)
        assert items
        assert all(b.gross is None for b in items)
        assert all(b.has_declared_deductions for b in items)

    def test_agrupa_por_transaccion(self, wompi_csv):
        items = build_transaction_breakdowns(wompi_csv)
        assert len(items) == 9  # 9 transacciones en los 4 CSV
        assert all(b.transaction_id.startswith("1203607-") for b in items)


class TestDescomposicionDelPos:
    """El POS liquida por venta, así que sí cierra a nivel transacción.

    Es el contraste que justifica `settlement_scope`: sin ese campo, un
    consumidor no puede distinguir "no cierra" de "acá no corresponde cerrar".
    """

    @pytest.fixture
    def pos(self):
        from conciliacion.config import POS

        return _ledger(POS, "pos", "*.txt", PosAsobancariaAdapter())

    def test_el_scope_es_por_transaccion(self, pos):
        items = build_transaction_breakdowns(pos)
        assert len(items) == 3
        assert {b.settlement_scope for b in items} == {"transaction"}

    def test_cada_venta_cierra_en_cero(self, pos):
        assert all(b.closes_to_zero for b in build_transaction_breakdowns(pos))

    def test_neto_esperado_es_bruto_menos_descuentos(self, pos):
        b = build_transaction_breakdowns(pos)[0]
        assert (
            b.net_expected["cents"]
            == b.gross["cents"] - b.total_deductions["cents"]
        )


class TestApi:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch, banco, wompi_csv):
        from fastapi.testclient import TestClient

        from conciliacion.api import main
        from conciliacion.storage.sqlite_repo import SqliteRepository

        db = tmp_path / "api.db"
        with SqliteRepository(db) as repo:
            repo.save_ledger(banco)
            repo.save_ledger(wompi_csv)

        monkeypatch.setenv("DATABASE_PATH", str(db))
        main._settings.cache_clear()
        yield TestClient(main.app)
        main._settings.cache_clear()

    def test_system(self, client):
        body = client.get("/api/system").json()
        assert body["contract_version"] == "1.0"
        assert {x["id"] for x in body["ledgers"]} == {"bancolombia", "wompi"}
        assert body["coverage"]["bancolombia"]["from"] == "2026-01-01"

    def test_movimientos_paginan(self, client):
        body = client.get("/api/ledgers/bancolombia/movements?limit=10").json()
        assert body["total"] == 214
        assert len(body["items"]) == 10

    def test_filtro_por_texto(self, client):
        body = client.get("/api/ledgers/bancolombia/movements?q=wompi&limit=100").json()
        assert body["total"] == 26
        assert all("WOMPI" in i["description"].upper() for i in body["items"])

    def test_filtro_por_kind(self, client):
        body = client.get("/api/ledgers/bancolombia/movements?kind=bank_credit").json()
        assert all(i["kind"] == "bank_credit" for i in body["items"])

    def test_extracto(self, client):
        body = client.get("/api/ledgers/bancolombia/statements/2026-04").json()
        assert body["chain_intact"] is True
        assert body["line_count"] == 110

    def test_extracto_inexistente_da_404(self, client):
        assert client.get("/api/ledgers/bancolombia/statements/2030-01").status_code == 404

    def test_ledger_sin_datos_da_404_con_instruccion(self, client):
        r = client.get("/api/ledgers/pos/movements")
        assert r.status_code == 404
        assert "conciliacion ingest" in r.json()["detail"]

    def test_movimiento_por_id(self, client):
        listado = client.get("/api/ledgers/bancolombia/movements?limit=1").json()
        mid = listado["items"][0]["id"]
        assert client.get(f"/api/ledgers/bancolombia/movements/{mid}").json()["id"] == mid

    def test_movimiento_inexistente(self, client):
        assert client.get("/api/ledgers/bancolombia/movements/mov_noexiste").status_code == 404

    def test_kinds(self, client):
        body = client.get("/api/kinds").json()
        assert "payment" in body["kinds"] and "settlement" in body["kinds"]

    def test_el_informe_se_sirve_como_markdown(self, client):
        """`text/plain` hacía que el navegador lo abriera como código fuente."""
        r = client.get("/api/reconciliation/flow/report.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert r.text.startswith("# ")
        # Sin `download` no baja nada: es lo que consume la vista del informe.
        assert "content-disposition" not in r.headers

    def test_download_lo_convierte_en_archivo(self, client):
        """El nombre es el mismo que escribe la CLI en `data/out/`: bajarlo de
        la web o generarlo por consola tiene que dar el mismo archivo."""
        r = client.get("/api/reconciliation/flow/report.md?download=1")
        assert (
            r.headers["content-disposition"]
            == 'attachment; filename="conciliacion-flujo-wompi-bancolombia.md"'
        )

    def test_el_informe_del_erp_tambien(self, client):
        r = client.get("/api/reconciliation/erp/wompi/report.md?download=1")
        assert r.headers["content-type"].startswith("text/markdown")
        assert (
            r.headers["content-disposition"]
            == 'attachment; filename="conciliacion-erp-wompi.md"'
        )
