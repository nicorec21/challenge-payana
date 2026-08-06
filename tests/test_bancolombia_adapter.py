"""Adapter de Bancolombia, contra los extractos reales.

Los fixtures son una copia congelada en `tests/fixtures/`, no `data/raw/`: un
test que lee el directorio de runtime se rompe cuando alguien baja un archivo
más.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from conciliacion.domain import Money, MovementKind
from conciliacion.ingest.adapters.bancolombia_pdf import (
    BancolombiaPdfAdapter,
    StatementIntegrityError,
    StatementHeader,
    StatementSummary,
    _Row,
    _verify,
)
from conciliacion.ingest.ports import RawRecord

FIXTURES = Path(__file__).parent / "fixtures" / "bancolombia"


def record(name: str) -> RawRecord:
    path = FIXTURES / name
    return RawRecord(
        locator=f"tests/fixtures/bancolombia/{name}",
        payload=path.read_bytes(),
        fetched_at=datetime.now(timezone.utc),
        metadata={"filename": name},
    )


@pytest.fixture
def adapter():
    return BancolombiaPdfAdapter()


@pytest.fixture
def abril(adapter):
    return list(adapter.parse(record("Extracto_Abril.pdf")))


@pytest.fixture
def enero(adapter):
    return list(adapter.parse(record("Extracto_Enero.pdf")))


class TestSniff:
    def test_reconoce_extracto_real(self, adapter):
        assert adapter.sniff(record("Extracto_Abril.pdf"))

    def test_rechaza_no_pdf(self, adapter):
        assert not adapter.sniff(RawRecord(locator="x.csv", payload=b"fecha,monto\n"))

    def test_rechaza_payload_no_binario(self, adapter):
        assert not adapter.sniff(RawRecord(locator="x", payload={"a": 1}))

    def test_no_explota_con_pdf_corrupto(self, adapter):
        """`sniff` debe decir que no, no romper: si tira excepción, el pipeline
        pierde la chance de probar otro adapter."""
        assert not adapter.sniff(RawRecord(locator="roto.pdf", payload=b"%PDF-1.4 basura"))


class TestParseo:
    def test_cantidad_de_movimientos(self, abril):
        assert len(abril) == 110

    def test_todos_al_ledger_correcto(self, abril):
        assert {m.ledger_id for m in abril} == {"bancolombia"}
        assert {m.source_id for m in abril} == {"bancolombia_pdf"}

    def test_signo_define_el_kind(self, abril):
        for m in abril:
            esperado = MovementKind.BANK_CREDIT if m.amount.amount > 0 else MovementKind.BANK_DEBIT
            assert m.kind is esperado

    def test_raw_ref_apunta_a_archivo_y_pagina(self, abril):
        assert all("#pagina=" in m.raw_ref for m in abril)
        assert abril[0].raw_ref.startswith("tests/fixtures/bancolombia/Extracto_Abril.pdf#")

    def test_conserva_el_saldo_en_metadata(self, abril):
        """El saldo entra en la clave de idempotencia; hay que poder auditarlo."""
        assert all("saldo" in m.metadata for m in abril)


class TestDerivacionDelAnio:
    def test_abril_2026(self, abril):
        assert {m.occurred_on.year for m in abril} == {2026}
        assert {m.occurred_on.month for m in abril} == {4}

    def test_enero_usa_hasta_no_desde(self, enero):
        """El extracto de enero dice `DESDE: 2025/12/31 HASTA: 2026/01/31`.
        Derivar el año del DESDE corre todo el extracto a 2025."""
        assert {m.occurred_on.year for m in enero} == {2026}
        assert {m.occurred_on.month for m in enero} == {1}


class TestLiquidacionesDeWompiVerificadas:
    """Los tres montos que se cruzaron contra los CSV de desembolso de Wompi.

    Cada uno es la suma de `total desembolsado` del archivo del mismo día:
        14-04 → 804.163,63   15-04 → 926.373,11   27-04 → 698.160,78
    """

    @pytest.mark.parametrize(
        "dia,monto",
        [(14, "804163.63"), (15, "926373.11"), (27, "698160.78")],
    )
    def test_aparece_en_la_fecha_exacta(self, abril, dia, monto):
        hits = [
            m for m in abril
            if m.occurred_on == date(2026, 4, dia) and m.amount == Money.parse(monto)
        ]
        assert len(hits) == 1
        assert "WOMPI" in hits[0].description.upper()

    def test_el_adapter_no_clasifica(self, abril):
        """Una línea que dice WOMPI se ingiere como crédito bancario común.

        Decidir que ES una liquidación de Wompi es del motor de conciliación,
        que además tiene que explicarlo. Si el adapter la etiquetara, esa
        conclusión entraría al sistema sin evidencia."""
        wompi = [m for m in abril if "WOMPI" in m.description.upper()]
        assert wompi
        assert {m.kind for m in wompi} == {MovementKind.BANK_CREDIT}
        assert MovementKind.SETTLEMENT not in {m.kind for m in abril}

    def test_dos_descripciones_distintas_para_wompi(self, abril):
        """La descripción cambia el 14/04/2026: `PAGO DE PROV` → `PAGO DE TERC`.

        Un matcher que use la descripción como llave pierde 53 de 58 líneas.
        Este test existe para que ese supuesto quede documentado y falle si
        alguien lo introduce."""
        descripciones = {m.description for m in abril if "WOMPI" in m.description.upper()}
        assert descripciones == {"PAGO DE PROV WOMPI S.A.S.", "PAGO DE TERC WOMPI S.A.S."}


class TestIdentidadEIdempotencia:
    def test_ids_unicos_dentro_del_extracto(self, abril):
        assert len({m.id for m in abril}) == len(abril)

    def test_ids_unicos_entre_extractos(self, abril, enero):
        assert not {m.id for m in abril} & {m.id for m in enero}

    def test_reparsear_da_los_mismos_ids(self, adapter, abril):
        otra = list(adapter.parse(record("Extracto_Abril.pdf")))
        assert [m.id for m in otra] == [m.id for m in abril]

    def test_lineas_identicas_se_distinguen_por_saldo(self, abril):
        """El 1/04 hay 16 líneas idénticas de `SERVICIO E-MAILS ENVIADOS
        -280,00`. Sin el saldo en la clave, colapsan a un solo movimiento y se
        pierden 15 cargos reales."""
        emails = [
            m for m in abril
            if m.description == "SERVICIO E-MAILS ENVIADOS"
            and m.occurred_on == date(2026, 4, 1)
            and m.amount == Money.parse("-280")
        ]
        assert len(emails) == 16
        assert len({m.id for m in emails}) == 16


class TestIntegridad:
    def test_la_cadena_de_saldos_se_reconstruye(self, abril):
        """Invariante del extracto, verificado desde los movimientos ya
        normalizados: no solo parseó, parseó bien."""
        saldos = [Money.parse(m.metadata["saldo"]) for m in abril]
        for anterior, actual, mov in zip(saldos, saldos[1:], abril[1:]):
            assert anterior + mov.amount == actual

    def test_cadena_rota_es_rechazada(self):
        header = StatementHeader("123", date(2026, 3, 31), date(2026, 4, 30))
        summary = StatementSummary(
            saldo_anterior=Money.parse("1000"),
            total_abonos=Money.parse("500"),
            total_cargos=Money.zero(),
            saldo_actual=Money.parse("1500"),
        )
        rows = [
            _Row(1, 10, date(2026, 4, 1), "OK", Money.parse("200"), Money.parse("1200")),
            # saldo deberia ser 1500, dice 9999 -> se perdio o se leyo mal una fila
            _Row(1, 20, date(2026, 4, 2), "ROTA", Money.parse("300"), Money.parse("9999")),
        ]
        with pytest.raises(StatementIntegrityError, match="Cadena de saldos rota"):
            _verify(header, summary, rows, "fixture")

    def test_resumen_que_no_cierra_es_rechazado(self):
        header = StatementHeader("123", date(2026, 3, 31), date(2026, 4, 30))
        summary = StatementSummary(
            saldo_anterior=Money.parse("1000"),
            total_abonos=Money.parse("500"),
            total_cargos=Money.zero(),
            saldo_actual=Money.parse("9999"),
        )
        rows = [_Row(1, 10, date(2026, 4, 1), "X", Money.parse("500"), Money.parse("1500"))]
        with pytest.raises(StatementIntegrityError, match="resumen no cierra"):
            _verify(header, summary, rows, "fixture")

    def test_total_abonos_que_no_suma_es_rechazado(self):
        """Detecta que falte un bloque entero de filas, caso que la cadena de
        saldos sola no ve si el bloque está al final."""
        header = StatementHeader("123", date(2026, 3, 31), date(2026, 4, 30))
        summary = StatementSummary(
            saldo_anterior=Money.parse("1000"),
            total_abonos=Money.parse("500"),
            total_cargos=Money.zero(),
            saldo_actual=Money.parse("1500"),
        )
        rows = [_Row(1, 10, date(2026, 4, 1), "X", Money.parse("200"), Money.parse("1200"))]
        with pytest.raises(StatementIntegrityError):
            _verify(header, summary, rows, "fixture")

    def test_extracto_vacio_es_rechazado(self):
        header = StatementHeader("123", date(2026, 3, 31), date(2026, 4, 30))
        summary = StatementSummary(Money.zero(), Money.zero(), Money.zero(), Money.zero())
        with pytest.raises(StatementIntegrityError, match="no tiene movimientos"):
            _verify(header, summary, [], "fixture")
