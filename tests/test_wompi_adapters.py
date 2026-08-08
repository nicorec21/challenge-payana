"""Los tres adapters de Wompi, y sobre todo: que juntos cierren en cero."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from conciliacion.config import WOMPI_FEES
from conciliacion.domain import Money, MovementKind, MovementStatus
from conciliacion.ingest.adapters.wompi_api import (
    WompiApiDisbursementsAdapter,
    WompiApiTransactionsAdapter,
)
from conciliacion.ingest.adapters.wompi_disbursement_csv import (
    DisbursementCsvIntegrityError,
    WompiDisbursementCsvAdapter,
)
from conciliacion.ingest.ports import IngestionError, RawRecord

FIXTURES = Path(__file__).parent / "fixtures" / "wompi"

# La transacción de las 21:22 COT, que en UTC cae al día siguiente.
TX_NOCTURNA = {
    "id": "1203607-1777515757-57617",
    "created_at": "2026-04-30T02:22:37.000Z",   # UTC
    "finalized_at": "2026-04-30T02:23:01.000Z",
    "amount_in_cents": 31754900,
    "currency": "COP",
    "reference": "kcmjn4jfa1dka4b2fmtj4j",
    "payment_method_type": "CARD",
    "status": "APPROVED",
    "status_message": None,
    "disbursement": {"id": 3201672, "amount_in_cents": 30342952, "status": "APPROVED"},
}

TX_RECHAZADA = {
    "id": "1203607-1776142911-29110",
    "created_at": "2026-04-14T05:01:00.000Z",
    "amount_in_cents": 35751300,
    "currency": "COP",
    "reference": "rechazada",
    "payment_method_type": "CARD",
    "status": "DECLINED",
    "status_message": "Fondos insuficientes",
    "disbursement": None,
}

DISBURSEMENT = {
    "id": 3131158,
    "amount_in_cents": 80416363,
    "status": "APPROVED",
    "created_at": "2026-04-14T13:32:42.087Z",
    "updated_at": "2026-04-14T23:11:02.000Z",
    "bank_account_type": "SAVINGS",
}


def api_record(payload, resource):
    return RawRecord(
        locator=f"data/raw/wompi/api/{resource}/p001.json#id={payload['id']}",
        payload=payload,
        metadata={"resource": resource, "redacted": True},
    )


def csv_record(name):
    path = FIXTURES / name
    return RawRecord(
        locator=f"tests/fixtures/wompi/{name}",
        payload=path.read_bytes(),
        metadata={"filename": name},
    )


# ── transacciones ───────────────────────────────────────────────────────────


class TestTransacciones:
    @pytest.fixture
    def adapter(self):
        return WompiApiTransactionsAdapter()

    def test_sniff(self, adapter):
        assert adapter.sniff(api_record(TX_NOCTURNA, "transactions"))
        assert not adapter.sniff(api_record(DISBURSEMENT, "disbursements"))
        assert not adapter.sniff(RawRecord(locator="x", payload=b"%PDF-"))

    def test_emite_un_payment_con_el_bruto(self, adapter):
        (m,) = adapter.parse(api_record(TX_NOCTURNA, "transactions"))
        assert m.kind is MovementKind.PAYMENT
        assert m.amount == Money.parse("317549")
        assert m.ledger_id == "wompi"
        assert m.reference == "kcmjn4jfa1dka4b2fmtj4j"

    def test_la_fecha_contable_es_en_hora_colombia(self, adapter):
        """`2026-04-30T02:22Z` es el 29 a las 21:22 en Bogotá. Agrupar por
        fecha UTC manda esta venta al batch del 30 y el día no cierra."""
        (m,) = adapter.parse(api_record(TX_NOCTURNA, "transactions"))
        assert m.occurred_on == date(2026, 4, 29)
        assert m.occurred_at.hour == 21 and m.occurred_at.minute == 22

    def test_guarda_el_link_al_desembolso(self, adapter):
        """Con este dato, agrupar pagos en su liquidación es un GROUP BY y no
        una búsqueda de subconjuntos."""
        (m,) = adapter.parse(api_record(TX_NOCTURNA, "transactions"))
        assert m.metadata["disbursement_id"] == 3201672

    def test_ingiere_las_rechazadas(self, adapter):
        """Explicar por qué una venta no llegó al banco requiere tenerla."""
        (m,) = adapter.parse(api_record(TX_RECHAZADA, "transactions"))
        assert m.status is MovementStatus.DECLINED
        assert m.metadata["disbursement_id"] is None
        assert not m.counts_for_reconciliation

    def test_transaccion_sin_monto_falla_con_contexto(self, adapter):
        rota = {k: v for k, v in TX_NOCTURNA.items() if k != "amount_in_cents"}
        with pytest.raises(IngestionError, match="mal formada"):
            list(adapter.parse(api_record(rota, "transactions")))


# ── desembolsos ─────────────────────────────────────────────────────────────


class TestDesembolsos:
    @pytest.fixture
    def adapter(self):
        return WompiApiDisbursementsAdapter()

    def test_emite_settlement_negativo(self, adapter):
        """Desde la cuenta de Wompi el giro al banco es una salida. El mismo
        hecho aparece positivo en el ledger de Bancolombia."""
        (m,) = adapter.parse(api_record(DISBURSEMENT, "disbursements"))
        assert m.kind is MovementKind.SETTLEMENT
        assert m.amount == Money.parse("-804163.63")

    def test_fecha_local(self, adapter):
        (m,) = adapter.parse(api_record(DISBURSEMENT, "disbursements"))
        assert m.occurred_on == date(2026, 4, 14)

    def test_el_id_es_la_referencia(self, adapter):
        (m,) = adapter.parse(api_record(DISBURSEMENT, "disbursements"))
        assert m.external_id == "3131158"
        assert m.metadata["disbursement_id"] == 3131158


# ── CSV ─────────────────────────────────────────────────────────────────────


class TestCsvDesembolso:
    @pytest.fixture
    def adapter(self):
        return WompiDisbursementCsvAdapter()

    NOMBRE_15 = "15-04-2026-disbursement-report-rs-203607-GEHg27R1IkiSqCXe7eXR2C0iSuA6sbdU000.csv"
    NOMBRE_14 = "14-04-2026-disbursement-report-rs-203607-CY5bzWT0ZGtFBxgp421VYoHN1Q6cP9l8000.csv"

    def test_sniff(self, adapter):
        assert adapter.sniff(csv_record(self.NOMBRE_14))
        assert not adapter.sniff(RawRecord(locator="x.pdf", payload=b"%PDF-1.4"))

    def test_no_emite_el_pago(self, adapter):
        """El bruto lo aporta el adapter de la API. Si este también lo emitiera,
        se contaría dos veces: distinto `source_id` ⇒ no deduplican."""
        movs = list(adapter.parse(csv_record(self.NOMBRE_14)))
        assert MovementKind.PAYMENT not in {m.kind for m in movs}
        assert MovementKind.SETTLEMENT not in {m.kind for m in movs}
        assert {m.kind for m in movs} == {MovementKind.FEE, MovementKind.TAX}

    def test_desglosa_la_fila(self, adapter):
        """La fila del 13-04 (840.763,00) tiene comisión, IVA y retefuente
        distintos de cero; reteica, reteiva e impoconsumo en cero."""
        movs = list(adapter.parse(csv_record(self.NOMBRE_14)))
        assert len(movs) == 3
        por_columna = {m.metadata["deduction"]: m.amount for m in movs}
        assert por_columna["comisión"] == Money.parse("-20157.93")
        assert por_columna["iva comisión"] == Money.parse("-3830.00")
        assert por_columna["retefuente"] == Money.parse("-12611.44")

    def test_no_emite_movimientos_en_cero(self, adapter):
        movs = list(adapter.parse(csv_record(self.NOMBRE_14)))
        assert all(not m.amount.is_zero for m in movs)
        assert "reteica" not in {m.metadata["deduction"] for m in movs}

    def test_separa_comision_de_impuesto(self, adapter):
        """Van a cuentas distintas en Odoo (530505 vs 240810/236500) y
        responden preguntas distintas: la comisión se negocia, la retención no."""
        movs = list(adapter.parse(csv_record(self.NOMBRE_14)))
        kinds = {m.metadata["deduction"]: m.kind for m in movs}
        assert kinds["comisión"] is MovementKind.FEE
        assert kinds["iva comisión"] is MovementKind.TAX
        assert kinds["retefuente"] is MovementKind.TAX

    def test_cuatro_transacciones_en_el_archivo_del_15(self, adapter):
        movs = list(adapter.parse(csv_record(self.NOMBRE_15)))
        assert len({m.metadata["transaction_id"] for m in movs}) == 4
        assert len(movs) == 12  # 4 tx x (comisión + iva + retefuente)

    def test_ids_unicos_por_columna(self, adapter):
        """Una transacción produce hasta 6 movimientos; cada uno necesita
        identidad propia o colapsan al deduplicar."""
        movs = list(adapter.parse(csv_record(self.NOMBRE_15)))
        assert len({m.id for m in movs}) == len(movs)
        assert all(m.external_id.count(":") == 1 for m in movs)

    def test_fecha_de_desembolso_sale_del_nombre_del_archivo(self, adapter):
        """No está adentro del CSV. Verificado contra el banco: coincide exacto
        con el día del crédito en 10 de 10 casos de abril."""
        movs = list(adapter.parse(csv_record(self.NOMBRE_14)))
        assert {m.metadata["settled_on"] for m in movs} == {"2026-04-14"}
        assert {m.occurred_on for m in movs} == {date(2026, 4, 13)}  # la venta fue el 13

    def test_nombre_sin_fecha_falla(self, adapter):
        record = RawRecord(
            locator="raro.csv",
            payload=(FIXTURES / self.NOMBRE_14).read_bytes(),
            metadata={"filename": "reporte.csv"},
        )
        with pytest.raises(IngestionError, match="fecha de desembolso"):
            list(adapter.parse(record))

    def test_la_identidad_se_valida(self, adapter):
        """bruto − descuentos = neto, al centavo. 9/9 en la muestra."""
        for name in (self.NOMBRE_14, self.NOMBRE_15):
            movs = list(adapter.parse(csv_record(name)))
            por_tx = {}
            for m in movs:
                por_tx.setdefault(m.metadata["transaction_id"], []).append(m)
            for tx, grupo in por_tx.items():
                bruto = Money.parse(grupo[0].metadata["declared_gross"])
                neto = Money.parse(grupo[0].metadata["declared_net"])
                descuentos = Money.sum(abs(m.amount) for m in grupo)
                assert bruto - descuentos == neto, tx

    def test_fila_que_no_cierra_es_rechazada(self, adapter):
        roto = (
            "id de la transaccion,fecha,referencia,monto,moneda,medio de pago,"
            "comisión,iva comisión,reteica,reteiva,retefuente,impoconsumo,"
            "total desembolsado,documento del pagador,tipo de documento del pagador\n"
            "1203607-1776117137-25637,13-04-2026 16:52,ref,840763.00,COP,CARD,"
            "20157.93,3830.00,0.00,0.00,12611.44,0.00,999999.99,,\n"
        )
        record = RawRecord(
            locator="roto.csv", payload=roto.encode(),
            metadata={"filename": "14-04-2026-x.csv"},
        )
        with pytest.raises(DisbursementCsvIntegrityError, match="no cierra"):
            list(adapter.parse(record))

    def test_fecha_incoherente_con_el_epoch_del_id_es_rechazada(self, adapter):
        """El ID trae `<comercio>-<epoch>-<seq>`. Reconstruir la fecha desde el
        epoch y compararla es un checksum gratis del parseo."""
        roto = (
            "id de la transaccion,fecha,referencia,monto,moneda,medio de pago,"
            "comisión,iva comisión,reteica,reteiva,retefuente,impoconsumo,"
            "total desembolsado,documento del pagador,tipo de documento del pagador\n"
            "1203607-1776117137-25637,01-01-2026 10:00,ref,840763.00,COP,CARD,"
            "20157.93,3830.00,0.00,0.00,12611.44,0.00,804163.63,,\n"
        )
        record = RawRecord(
            locator="roto.csv", payload=roto.encode(),
            metadata={"filename": "14-04-2026-x.csv"},
        )
        with pytest.raises(IngestionError, match="epoch del ID"):
            list(adapter.parse(record))

    def test_faltan_columnas(self, adapter):
        record = RawRecord(
            locator="x.csv", payload=b"a,b,c\n1,2,3\n", metadata={"filename": "14-04-2026-x.csv"}
        )
        with pytest.raises(IngestionError, match="Faltan columnas"):
            list(adapter.parse(record))


# ── auditoría contra el tarifario ───────────────────────────────────────────


class TestAuditoriaDelTarifario:
    """Que un cambio de tarifa entre sin romper nada es correcto. Que entre sin
    que nadie se entere, no.

    El CSV manda: sus montos se ingieren tal cual, así que el sistema se adapta
    solo a una tarifa nueva. Pero los descuentos se **leen** en 4 de 56 días y
    se **infieren** en el resto con `WOMPI_FEES`; si la config queda vieja, esa
    mayoría se calcula mal y el residuo aparece sin causa visible. La auditoría
    es lo que une el síntoma con el motivo.
    """

    TODOS = (
        TestCsvDesembolso.NOMBRE_14,
        TestCsvDesembolso.NOMBRE_15,
        "27-04-2026-disbursement-report-rs-203607-acwL6FXvd6b5KsYvovUYPe5EKHCfCYLc000.csv",
        "04-05-2026-disbursement-report-rs-203607-MVDjQLP290BzBSdodr5pIEDENWBanq7M000.csv",
    )

    def _con_tarifario(self, **cambios):
        """Adapter que audita contra una variante del tarifario vigente."""
        alterado = replace(WOMPI_FEES[0], **cambios)
        return WompiDisbursementCsvAdapter(lambda medio, dia: alterado)

    def test_los_cuatro_csv_pasan_limpio_con_el_tarifario_vigente(self):
        """El 9/9 exacto de ADR-0005, ahora como test en vez de como afirmación.

        Si este falla, o cambió la tarifa real o se rompió una fórmula."""
        adapter = WompiDisbursementCsvAdapter()
        for name in self.TODOS:
            assert list(adapter.audit(csv_record(name))) == [], name

    def test_un_cambio_de_retefuente_no_pasa_callado(self):
        """1,5% → 2,5%: exactamente el escenario "cambia la regulación"."""
        adapter = self._con_tarifario(retefuente_rate=Decimal("0.025"))
        avisos = list(adapter.audit(csv_record(TestCsvDesembolso.NOMBRE_15)))
        assert len(avisos) == 1
        assert "retefuente" in avisos[0]

    def test_el_aviso_distingue_cambio_de_tarifa_de_caso_suelto(self):
        """Todas las filas desviadas es un cambio de tarifa; una sola es una
        tarifa negociada o un error de carga. Se arreglan al revés, así que el
        aviso tiene que decir cuál de las dos es."""
        todas = self._con_tarifario(commission_rate=Decimal("0.03"))
        aviso_global = next(iter(todas.audit(csv_record(TestCsvDesembolso.NOMBRE_15))))
        assert "TODAS las filas" in aviso_global
        assert "WOMPI_FEES" in aviso_global

        # El archivo del 27-04 consolida el fin de semana: 3 filas en 3 días
        # distintos, así que un tarifario por fecha desvía solo una.
        base, otro = WOMPI_FEES[0], replace(WOMPI_FEES[0], commission_fixed=Money(50_000))
        una_sola = WompiDisbursementCsvAdapter(
            lambda medio, dia: otro if dia == date(2026, 4, 25) else base
        )
        aviso_puntual = next(iter(una_sola.audit(csv_record(self.TODOS[2]))))
        assert "1 de 3 filas" in aviso_puntual
        assert "TODAS" not in aviso_puntual

    def test_medio_de_pago_sin_tarifario_se_avisa_en_vez_de_asumirse(self):
        """Un medio nuevo (NEQUI, PSE) no tiene por qué cobrar como CARD. Que no
        haya tarifario no es un error: es que no se puede verificar, y esa
        diferencia tiene que llegar a quien lee el reporte."""
        adapter = WompiDisbursementCsvAdapter(lambda medio, dia: None)
        avisos = list(adapter.audit(csv_record(TestCsvDesembolso.NOMBRE_15)))
        assert len(avisos) == 1
        assert "CARD" in avisos[0] and "sin tarifario" in avisos[0]

    def test_el_dato_declarado_se_ingiere_igual_aunque_el_tarifario_no_coincida(self):
        """La fuente es la verdad; la config es la hipótesis. El adapter avisa,
        NO corrige: corregir haría que el sistema no pueda descubrir nunca que
        se equivocó (ADR-0005)."""
        adapter = self._con_tarifario(retefuente_rate=Decimal("0.99"))
        movs = list(adapter.parse(csv_record(TestCsvDesembolso.NOMBRE_14)))
        por_columna = {m.metadata["deduction"]: m.amount for m in movs}
        assert por_columna["retefuente"] == Money.parse("-12611.44")  # el del CSV

    def test_auditar_no_toca_los_movimientos(self):
        """El aviso no se persiste. `Movement` es inmutable, así que un desvío
        guardado en `metadata` seguiría afirmándose después de corregir la
        config. Auditar en cada corrida siempre habla del ahora."""
        adapter = self._con_tarifario(retefuente_rate=Decimal("0.025"))
        movs = list(adapter.parse(csv_record(TestCsvDesembolso.NOMBRE_14)))
        assert not any("tarif" in k for m in movs for k in m.metadata)


# ── los tres juntos ─────────────────────────────────────────────────────────


class TestElLedgerCierraEnCero:
    """La prueba de que el reparto disjunto entre los tres adapters es correcto.

    Para una transacción liquidada:
        +bruto  −comisión  −impuestos  −neto  =  0
    """

    def test_transaccion_liquidada_cierra(self):
        """La tx del 13-04: bruto 840.763,00, neto 804.163,63."""
        tx = {
            "id": "1203607-1776117137-25637",
            "created_at": "2026-04-13T21:52:17.000Z",
            "amount_in_cents": 84076300,
            "currency": "COP",
            "reference": "d02m6hzrinf727x6teynra",
            "payment_method_type": "CARD",
            "status": "APPROVED",
            "disbursement": {"id": 3131158, "amount_in_cents": 80416363, "status": "APPROVED"},
        }
        pago = list(WompiApiTransactionsAdapter().parse(api_record(tx, "transactions")))
        descuentos = list(
            WompiDisbursementCsvAdapter().parse(csv_record(TestCsvDesembolso.NOMBRE_14))
        )
        giro = list(WompiApiDisbursementsAdapter().parse(api_record(DISBURSEMENT, "disbursements")))

        total = Money.sum(m.amount for m in [*pago, *descuentos, *giro])
        assert total == Money.zero(), f"el ledger no cierra: quedó {total}"

    def test_no_hay_solapamiento_de_kinds(self):
        """Si dos adapters emitieran el mismo kind para el mismo hecho, el
        bruto se contaría dos veces: tienen distinto `source_id`, así que la
        deduplicación no los une."""
        tx = {**TX_NOCTURNA}
        kinds_tx = {m.kind for m in WompiApiTransactionsAdapter().parse(api_record(tx, "transactions"))}
        kinds_csv = {
            m.kind for m in WompiDisbursementCsvAdapter().parse(csv_record(TestCsvDesembolso.NOMBRE_14))
        }
        kinds_dis = {
            m.kind for m in WompiApiDisbursementsAdapter().parse(api_record(DISBURSEMENT, "disbursements"))
        }
        assert not kinds_tx & kinds_csv
        assert not kinds_tx & kinds_dis
        assert not kinds_csv & kinds_dis
