"""Pipeline de ingesta end-to-end: connector → sniff → adapter → ledger.

Es la prueba de la Fase 1: `GenerarLedger(Cuenta)` + `RegistrarMovimientos(DataSource, Ledger)`
sobre los extractos reales.
"""

from datetime import date
from pathlib import Path

import pytest

from conciliacion.config import BANCOLOMBIA, WOMPI
from conciliacion.domain import Money, MovementKind
from conciliacion.ingest.adapters.bancolombia_pdf import BancolombiaPdfAdapter
from conciliacion.ingest.connectors.local_file import LocalFileConnector
from conciliacion.ingest.ports import RawRecord
from conciliacion.ingest.registry import SourceRegistry, SourceSpec, ingest, ingest_all

FIXTURES = Path(__file__).parent / "fixtures" / "bancolombia"


@pytest.fixture
def spec():
    return SourceSpec(
        name="bancolombia_extractos",
        connector=LocalFileConnector(FIXTURES, "*.pdf", connector_id="local_pdf"),
        adapters=(BancolombiaPdfAdapter(),),
    )


@pytest.fixture
def registry(spec):
    reg = SourceRegistry()
    reg.register_account(BANCOLOMBIA)
    reg.register_account(WOMPI)
    reg.register_source(spec)
    return reg


class TestConnector:
    def test_emite_un_registro_por_archivo(self):
        records = list(LocalFileConnector(FIXTURES, "*.pdf").fetch())
        assert len(records) == 2

    def test_orden_estable(self):
        """El orden del filesystem no es reproducible entre sistemas; la
        ingesta sí tiene que serlo."""
        a = [r.locator for r in LocalFileConnector(FIXTURES, "*.pdf").fetch()]
        b = [r.locator for r in LocalFileConnector(FIXTURES, "*.pdf").fetch()]
        assert a == b == sorted(a)

    def test_locator_es_ruta_relativa_al_repo(self):
        record = next(iter(LocalFileConnector(FIXTURES, "*.pdf").fetch()))
        assert record.locator.startswith("tests/fixtures/bancolombia/")
        assert not Path(record.locator).is_absolute()

    def test_entrega_bytes_no_rutas(self):
        """Que el adapter reciba bytes es lo que lo mantiene sin I/O y
        testeable con un fixture en memoria."""
        record = next(iter(LocalFileConnector(FIXTURES, "*.pdf").fetch()))
        assert isinstance(record.payload, bytes)

    def test_directorio_inexistente_no_explota(self):
        assert list(LocalFileConnector(FIXTURES / "nope", "*.pdf").fetch()) == []

    def test_patron_filtra(self):
        assert list(LocalFileConnector(FIXTURES, "*.csv").fetch()) == []


class TestIngesta:
    def test_ingiere_los_dos_extractos(self, registry, spec):
        ledger = registry.new_ledger("bancolombia")
        report = ingest(spec, ledger, strict=True)

        assert report.records_read == 2
        assert report.ingested == len(ledger) == 214  # 110 abril + 104 enero
        assert report.skipped == []
        assert report.ok

    def test_reingerir_no_duplica(self, registry, spec):
        """Idempotencia: correr la ingesta dos veces deja el ledger igual."""
        ledger = registry.new_ledger("bancolombia")
        primera = ingest(spec, ledger)
        segunda = ingest(spec, ledger)

        assert primera.ingested == 214 and primera.duplicates == 0
        assert segunda.ingested == 0 and segunda.duplicates == 214
        assert len(ledger) == 214

    def test_ingest_all_arma_el_ledger(self, registry):
        ledger, reports = ingest_all(registry, "bancolombia", strict=True)
        assert len(ledger) == 214
        assert all(r.ok for r in reports)

    def test_ledger_sin_fuentes_queda_vacio(self, registry):
        ledger, reports = ingest_all(registry, "wompi")
        assert len(ledger) == 0 and reports == []

    def test_registra_que_adapter_uso(self, registry, spec):
        report = ingest(spec, registry.new_ledger("bancolombia"))
        assert report.adapters_used == {"bancolombia_pdf": 214}


class TestSeleccionDeAdapter:
    def test_registro_sin_adapters_falla(self):
        with pytest.raises(ValueError, match="sin adapters"):
            SourceSpec(name="x", connector=LocalFileConnector(FIXTURES), adapters=())

    def test_fuente_de_ledger_no_registrado_falla(self, spec):
        reg = SourceRegistry()
        reg.register_account(WOMPI)
        with pytest.raises(ValueError, match="no registrado"):
            reg.register_source(spec)

    def test_registro_no_reconocido_se_reporta_y_no_frena(self, registry):
        """Ningún adapter reconoce el archivo → queda en `skipped` y la ingesta
        sigue. En conciliación conviene una corrida parcial con el faltante
        señalado antes que ninguna corrida."""
        class Basura:
            connector_id = "basura"
            def fetch(self, window=None):
                yield RawRecord(locator="basura.txt", payload=b"no soy un extracto")

        spec = SourceSpec("basura", Basura(), (BancolombiaPdfAdapter(),))
        report = ingest(spec, registry.new_ledger("bancolombia"))

        assert report.ingested == 0
        assert len(report.skipped) == 1
        assert not report.ok

    def test_strict_convierte_el_skip_en_error(self, registry):
        from conciliacion.ingest.ports import IngestionError

        class Basura:
            connector_id = "basura"
            def fetch(self, window=None):
                yield RawRecord(locator="basura.txt", payload=b"no soy un extracto")

        spec = SourceSpec("basura", Basura(), (BancolombiaPdfAdapter(),))
        with pytest.raises(IngestionError):
            ingest(spec, registry.new_ledger("bancolombia"), strict=True)


class TestLedgerResultante:
    @pytest.fixture
    def ledger(self, registry):
        return ingest_all(registry, "bancolombia", strict=True)[0]

    def test_orden_cronologico(self, ledger):
        fechas = [m.occurred_on for m in ledger]
        assert fechas == sorted(fechas)

    def test_cubre_enero_y_abril(self, ledger):
        assert ledger.date_range == (date(2026, 1, 1), date(2026, 4, 30))

    def test_las_liquidaciones_de_wompi_estan(self, ledger):
        """Los tres montos cruzados contra los CSV de desembolso."""
        abril = ledger.between(date(2026, 4, 1), date(2026, 4, 30))
        montos = {m.amount for m in abril if "WOMPI" in m.description.upper()}
        for esperado in ("804163.63", "926373.11", "698160.78"):
            assert Money.parse(esperado) in montos

    def test_hay_creditos_y_debitos(self, ledger):
        kinds = {m.kind for m in ledger}
        assert kinds == {MovementKind.BANK_CREDIT, MovementKind.BANK_DEBIT}

    def test_el_ruido_domina(self, ledger):
        """58 líneas de Wompi sobre 426 en los 4 extractos. Acá, sobre 2.

        Documenta un requisito del reporte: no se puede listar 'movimientos
        bancarios sin conciliar' porque serían mayormente irrelevantes."""
        wompi = [m for m in ledger if "WOMPI" in m.description.upper()]
        assert len(wompi) == 26  # 10 abril + 16 enero
        assert len(wompi) / len(ledger) < 0.15
