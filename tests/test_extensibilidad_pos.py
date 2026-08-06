"""Demostración de extensibilidad: sumar el POS bancario.

El enunciado pide *"¿cuánto código nuevo hace falta? Mostralo"*. Este archivo es
la prueba ejecutable de la respuesta: el POS entra por el mismo pipeline, con el
mismo connector, sin tocar el dominio ni el motor.

La data del fixture es sintética —el POS está fuera de alcance y no tenemos
archivos reales—. El layout no lo es: `Asobancaria 2001` es un formato bancario
colombiano, ofrecido por el propio dashboard de Wompi como alternativa al CSV.
Lo que se mide es el costo de extender, y eso es medible con data sintética.
"""

from datetime import date
from pathlib import Path

import pytest

from conciliacion.config import POS, settlement_policy_for
from conciliacion.domain import Money, MovementKind
from conciliacion.ingest.adapters.pos_asobancaria import (
    PosAsobancariaAdapter,
    PosFileIntegrityError,
)
from conciliacion.ingest.connectors.local_file import LocalFileConnector
from conciliacion.ingest.ports import RawRecord
from conciliacion.ingest.registry import SourceRegistry, SourceSpec, ingest_all
from conciliacion.reconcile.calendar import BusinessCalendar
from conciliacion.storage.sqlite_repo import SqliteRepository

FIXTURES = Path(__file__).parent / "fixtures" / "pos"
ARCHIVO = "20260415-POS-19300008472.txt"


def record(name: str = ARCHIVO) -> RawRecord:
    return RawRecord(
        locator=f"tests/fixtures/pos/{name}",
        payload=(FIXTURES / name).read_bytes(),
        metadata={"filename": name},
    )


@pytest.fixture
def adapter():
    return PosAsobancariaAdapter()


@pytest.fixture
def registry():
    """Registro del POS. Estas 8 líneas son TODO lo que hay que sumar a
    `sources.py` para incorporar el canal en producción."""
    reg = SourceRegistry()
    reg.register_account(POS)
    reg.register_source(
        SourceSpec(
            name="pos_liquidaciones",
            connector=LocalFileConnector(FIXTURES, "*.txt", connector_id="local_pos"),
            adapters=(PosAsobancariaAdapter(),),
        )
    )
    return reg


class TestElConnectorSeReusa:
    def test_cero_lineas_de_connector_nuevo(self, registry):
        """Formato nuevo, transporte conocido: el `LocalFileConnector` que ya
        usan los extractos PDF y los CSV de Wompi sirve tal cual.

        Es el beneficio concreto de separar Connector de Adapter (ADR-0002):
        con una abstracción fusionada, este caso costaría una clase entera."""
        (spec,) = registry.sources_for("pos")
        assert isinstance(spec.connector, LocalFileConnector)


class TestParseo:
    def test_sniff_reconoce_ancho_fijo(self, adapter):
        assert adapter.sniff(record())
        assert not adapter.sniff(RawRecord(locator="x.pdf", payload=b"%PDF-1.4 ..."))
        assert not adapter.sniff(RawRecord(locator="x.csv", payload=b"id,fecha,monto\n"))

    def test_decimales_implicitos(self, adapter):
        """Ancho fijo sin punto decimal: `000000025000000` son $250.000,00.
        Money guarda centavos, así que el campo entra directo."""
        movs = list(adapter.parse(record()))
        ventas = [m for m in movs if m.kind is MovementKind.PAYMENT]
        assert Money.parse("250000") in {m.amount for m in ventas}

    def test_tres_ventas_cuatro_movimientos_cada_una(self, adapter):
        movs = list(adapter.parse(record()))
        assert len(movs) == 12
        assert len({m.reference for m in movs}) == 3

    def test_el_ledger_cierra_en_cero(self, adapter):
        """Mismo invariante que Wompi: venta − comisión − IVA − liquidación = 0."""
        movs = list(adapter.parse(record()))
        assert Money.sum(m.amount for m in movs) == Money.zero()

    def test_separa_venta_comision_iva_y_liquidacion(self, adapter):
        movs = list(adapter.parse(record()))
        kinds = {m.kind for m in movs}
        assert kinds == {
            MovementKind.PAYMENT, MovementKind.FEE,
            MovementKind.TAX, MovementKind.SETTLEMENT,
        }

    def test_la_liquidacion_va_en_su_propia_fecha(self, adapter):
        """La venta ocurre un día y la liquidación otro; cada movimiento se
        registra en el suyo. Es lo que después permite conciliar contra el banco."""
        movs = list(adapter.parse(record()))
        venta = next(m for m in movs if m.kind is MovementKind.PAYMENT)
        liq = next(m for m in movs if m.kind is MovementKind.SETTLEMENT
                   and m.reference == venta.reference)
        assert venta.occurred_on == date(2026, 4, 13)
        assert liq.occurred_on == date(2026, 4, 15)


class TestIntegridad:
    """Mismo criterio que el extracto bancario (ADR-0007): el archivo trae sus
    propios totales de control y si no cierran se rechaza entero."""

    def _mutar(self, reemplazo: tuple[str, str]) -> RawRecord:
        texto = (FIXTURES / ARCHIVO).read_text()
        return RawRecord(
            locator="mutado.txt",
            payload=texto.replace(*reemplazo).encode(),
            metadata={"filename": ARCHIVO},
        )

    def test_total_bruto_que_no_cierra(self, adapter):
        roto = self._mutar(("990000000300000008500000", "990000000300000009500000"))
        with pytest.raises(PosFileIntegrityError, match="Total bruto"):
            list(adapter.parse(roto))

    def test_cantidad_de_registros_que_no_coincide(self, adapter):
        roto = self._mutar(("9900000003", "9900000009"))
        with pytest.raises(PosFileIntegrityError, match="declara 9 registros"):
            list(adapter.parse(roto))

    def test_falta_el_registro_de_control(self, adapter):
        texto = "\n".join(
            linea
            for linea in (FIXTURES / ARCHIVO).read_text().splitlines()
            if not linea.startswith("99")
        )
        rec = RawRecord(locator="sin_control.txt", payload=texto.encode(), metadata={})
        with pytest.raises(PosFileIntegrityError, match="Falta el registro de control"):
            list(adapter.parse(rec))


class TestCadenciaDistintaEsConfiguracion:
    """La parte que realmente prueba algo.

    Un formato nuevo lo resuelve cualquier diseño con interfaces. Una **cadencia
    de liquidación distinta** es lo que revela si el motor estaba acoplado a
    Wompi.
    """

    def test_la_cadencia_es_un_parametro(self):
        assert settlement_policy_for("wompi").settlement_lag_business_days == 1
        assert settlement_policy_for("pos").settlement_lag_business_days == 2

    def test_sumar_el_pos_no_agrego_reglas(self):
        """T+2 se expresa como un entero en `SETTLEMENT_POLICIES`, no como una
        rama nueva en el motor."""
        from conciliacion.config import SETTLEMENT_POLICIES
        assert set(SETTLEMENT_POLICIES) == {"wompi", "pos"}

    def test_canal_desconocido_tiene_default_sensato(self):
        """Un canal sin política declarada no rompe el sistema."""
        assert settlement_policy_for("ubereats").settlement_lag_business_days == 1

    def test_la_cadencia_declarada_coincide_con_el_fixture(self):
        """T+2 hábiles: la venta del lunes 13/04 liquida el miércoles 15/04."""
        cal = BusinessCalendar("CO")
        politica = settlement_policy_for("pos")
        esperado = cal.shift(date(2026, 4, 13), politica.settlement_lag_business_days)
        assert esperado == date(2026, 4, 15)


class TestPipelineCompleto:
    def test_ingesta_por_el_mismo_pipeline(self, registry):
        ledger, reports = ingest_all(registry, "pos", strict=True)
        assert len(ledger) == 12
        assert all(r.ok for r in reports)
        assert ledger.balance() == Money.zero()

    def test_persiste_en_la_misma_base(self, registry, tmp_path):
        ledger, _ = ingest_all(registry, "pos", strict=True)
        with SqliteRepository(tmp_path / "pos.db") as repo:
            assert repo.save_ledger(ledger) == 12
            recuperado = repo.load_ledger("pos")
        assert [m.id for m in recuperado] == [m.id for m in ledger]

    def test_idempotente_como_las_demas_fuentes(self, registry):
        primera, _ = ingest_all(registry, "pos", strict=True)
        ledger = registry.new_ledger("pos")
        from conciliacion.ingest.registry import ingest
        (spec,) = registry.sources_for("pos")
        ingest(spec, ledger)
        segunda = ingest(spec, ledger)
        assert segunda.ingested == 0 and segunda.duplicates == 12

    def test_el_dominio_no_cambio(self):
        """El POS no agregó ningún `MovementKind`, `MovementStatus` ni campo.

        Es el test que respalda la afirmación del README: sumar un canal cuesta
        1 adapter + 1 fixture + 8 líneas de registro, y **0 líneas de dominio**.
        Si alguien tuviera que agregar un kind para el próximo canal, el modelo
        estaría filtrando la fuente hacia adentro y este test lo diría."""
        movs = list(PosAsobancariaAdapter().parse(record()))
        assert {m.kind for m in movs} <= set(MovementKind)
        assert all(m.ledger_id == "pos" for m in movs)
