"""Conciliación de flujo canal → banco.

Los tests se dividen en dos: casos construidos a mano —donde se controla cada
variable y se verifica una regla— y una corrida sobre los datos reales del
challenge, que es la que valida que el motor sirva para algo.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from conciliacion.config import BANCOLOMBIA, WOMPI, SettlementPolicy
from conciliacion.domain import (
    Account,
    AdjustmentKind,
    Confidence,
    EvidenceSource,
    Ledger,
    Money,
    Movement,
    MovementKind,
    MovementStatus,
)
from conciliacion.ingest.adapters.bancolombia_pdf import BancolombiaPdfAdapter
from conciliacion.ingest.adapters.wompi_disbursement_csv import WompiDisbursementCsvAdapter
from conciliacion.ingest.connectors.local_file import LocalFileConnector
from conciliacion.ingest.registry import SourceRegistry, SourceSpec, ingest_all
from conciliacion.reconcile.calendar import BusinessCalendar
from conciliacion.reconcile.flow.engine import reconcile_flow
from conciliacion.reconcile.flow.findings import Coverage, FlowStatus

FIXTURES = Path(__file__).parent / "fixtures"
CAL = BusinessCalendar("CO")

#: Cobertura amplia para los escenarios armados a mano.
#:
#: Se pasa explícita porque derivarla del rango de movimientos es demasiado
#: estrecho en un ledger de dos filas: un banco con un solo crédito el 30/04
#: "no cubriría" el 14/04, y todo caería en OUT_OF_COVERAGE. En producción el
#: dato bueno lo tiene la ingesta, no el ledger.
TODO_2026 = Coverage(
    channel=(date(2026, 1, 1), date(2026, 12, 31)),
    bank=(date(2026, 1, 1), date(2026, 12, 31)),
)


# ── constructores de escenarios ─────────────────────────────────────────────


def canal(*movs: Movement) -> Ledger:
    led = Ledger(Account("wompi", "Canal", role="channel"))
    led.extend(movs)
    return led


def banco(*movs: Movement) -> Ledger:
    led = Ledger(Account("bancolombia", "Banco", role="bank"))
    led.extend(movs)
    return led


def venta(ext: str, day: date, monto: str, disbursement: str, **kw) -> Movement:
    return Movement(
        ledger_id="wompi", source_id="api_tx", external_id=ext,
        occurred_on=day, amount=Money.parse(monto), kind=MovementKind.PAYMENT,
        status=kw.pop("status", MovementStatus.APPROVED),
        metadata={"disbursement_id": disbursement, "payment_method_type": "CARD", **kw},
    )


def descuento(tx: str, columna: str, monto: str, kind: MovementKind) -> Movement:
    return Movement(
        ledger_id="wompi", source_id="csv", external_id=f"{tx}:{columna}",
        occurred_on=date(2026, 4, 13), amount=-Money.parse(monto), kind=kind,
        metadata={"transaction_id": tx, "deduction": columna},
    )


def giro(ext: str, day: date, monto: str, disbursement: str) -> Movement:
    return Movement(
        ledger_id="wompi", source_id="api_dis", external_id=ext,
        occurred_on=day, amount=-Money.parse(monto), kind=MovementKind.SETTLEMENT,
        metadata={"disbursement_id": disbursement},
    )


def credito(ext: str, day: date, monto: str, desc: str = "PAGO DE TERC WOMPI S.A.S.") -> Movement:
    return Movement(
        ledger_id="bancolombia", source_id="pdf", external_id=ext,
        occurred_on=day, amount=Money.parse(monto), kind=MovementKind.BANK_CREDIT,
        description=desc,
    )


# ── el caso del enunciado ───────────────────────────────────────────────────


class TestElEjemploDelEnunciado:
    """Tres pagos de 100, 200 y 150; comisión de 10; el banco acredita 440."""

    @pytest.fixture
    def escenario(self):
        d = date(2026, 4, 13)
        c = canal(
            venta("T1", d, "100", "D1"),
            venta("T2", d, "200", "D1"),
            venta("T3", d, "150", "D1"),
            descuento("T1", "comisión", "2.22", MovementKind.FEE),
            descuento("T2", "comisión", "4.45", MovementKind.FEE),
            descuento("T3", "comisión", "3.33", MovementKind.FEE),
            giro("D1", date(2026, 4, 14), "440", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 14), "440"))
        return reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026)

    def test_concilia(self, escenario):
        (f,) = escenario.of(FlowStatus.MATCHED)
        assert f.settlement_amount == Money.parse("440")
        assert f.bank_amount == Money.parse("440")
        assert f.difference == Money.zero()

    def test_la_explicacion_relaciona_las_tres_ventas_con_el_credito(self, escenario):
        """«ese +$440 del banco corresponde a la liquidación, compuesta por esos
        tres pagos menos comisiones e impuestos»."""
        (f,) = escenario.of(FlowStatus.MATCHED)
        assert len(f.transaction_ids) == 3
        assert f.explanation.gross == Money.parse("450")
        assert f.explanation.net == Money.parse("440")
        assert len(f.explanation.target_movement_ids) == 1

    def test_la_explicacion_dice_de_donde_salio_cada_ajuste(self, escenario):
        (f,) = escenario.of(FlowStatus.MATCHED)
        assert f.explanation.adjustments
        assert all(a.source is EvidenceSource.DECLARED for a in f.explanation.adjustments)
        assert f.explanation.adjustments_total == Money.parse("10")

    def test_la_explicacion_cierra(self, escenario):
        """`gross − ajustes == net`: la explicación se sostiene sola."""
        (f,) = escenario.of(FlowStatus.MATCHED)
        assert f.explanation.is_balanced
        assert f.explanation.unexplained is None

    def test_confianza_maxima_con_descuentos_declarados(self, escenario):
        (f,) = escenario.of(FlowStatus.MATCHED)
        assert f.confidence is Confidence.EXACT

    def test_trazabilidad_del_pago_individual(self, escenario):
        """«si miro el pago de $100, sus fondos terminaron dentro de ese +$440»."""
        (f,) = escenario.of(FlowStatus.MATCHED)
        pago = next(t for t in f.transaction_ids)
        assert pago in f.explanation.source_movement_ids
        assert f.bank_movement_id in f.explanation.target_movement_ids


# ── reglas ──────────────────────────────────────────────────────────────────


class TestDeclaradoVsInferido:
    def test_sin_desglose_los_ajustes_salen_inferidos(self):
        d = date(2026, 4, 13)
        c = canal(
            venta("T1", d, "840763", "D1"),
            giro("D1", date(2026, 4, 14), "804163.63", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 14), "804163.63"))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)

        assert {a.source for a in f.explanation.adjustments} == {EvidenceSource.INFERRED}
        assert {a.kind for a in f.explanation.adjustments} == {
            AdjustmentKind.COMMISSION, AdjustmentKind.TAX, AdjustmentKind.WITHHOLDING,
        }

    def test_el_tarifario_reproduce_el_neto_real(self):
        """Los ajustes inferidos suman exactamente la diferencia bruto − giro
        en este caso real (tomado del CSV del 14-04)."""
        d = date(2026, 4, 13)
        c = canal(
            venta("T1", d, "840763", "D1"),
            giro("D1", date(2026, 4, 14), "804163.63", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 14), "804163.63"))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)
        assert f.explanation.adjustments_total == Money.parse("36599.37")
        assert f.explanation.is_balanced

    def test_declarado_da_mas_confianza_que_inferido(self):
        """La jerarquía DECLARED > INFERRED no es estética: está medida
        (9/9 exacto vs 47/55 con ±$0,01)."""
        d, s = date(2026, 4, 13), date(2026, 4, 14)
        sin = canal(venta("T1", d, "840763", "D1"), giro("D1", s, "804163.63", "D1"))
        con = canal(
            venta("T1", d, "840763", "D1"),
            descuento("T1", "comisión", "20157.93", MovementKind.FEE),
            descuento("T1", "iva comisión", "3830.00", MovementKind.TAX),
            descuento("T1", "retefuente", "12611.44", MovementKind.TAX),
            giro("D1", s, "804163.63", "D1"),
        )
        b = lambda: banco(credito("B1", s, "804163.63"))  # noqa: E731

        (inferido,) = reconcile_flow(sin, b(), calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)
        (declarado,) = reconcile_flow(con, b(), calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)
        assert declarado.confidence is Confidence.EXACT
        assert inferido.confidence is Confidence.HIGH


class TestMedioDePagoSinTarifario:
    """No poder calcular un descuento no es haber calculado que no hay descuento.

    Codificar ese caso como cero hacía que el motor conciliara un giro cuya
    composición no había verificado, lo puntuara con confianza alta y el informe
    dijera *"todo el dinero del período está explicado"*. La plata desaparecía
    del reporte, no del banco.
    """

    def _un_giro(self, metodo: str, bruto: str, neto: str):
        d, s = date(2026, 4, 13), date(2026, 4, 14)
        c = canal(
            venta("T1", d, bruto, "D1", payment_method_type=metodo),
            giro("D1", s, neto, "D1"),
        )
        b = banco(credito("B1", s, neto))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).findings
        return f

    def test_el_faltante_no_se_da_por_explicado(self):
        f = self._un_giro("NEQUI", "1000000", "900000")
        assert f.status is FlowStatus.MATCHED   # la plata sí llegó al banco
        assert f.explanation.unexplained == Money.parse("100000")

    def test_no_puede_tener_confianza_alta(self):
        """Alta significa 'el desglose se estimó con una regla conocida'. Sin
        tarifario no hay regla, así que no hay nada que respalde ese nivel."""
        f = self._un_giro("NEQUI", "1000000", "900000")
        assert f.confidence is Confidence.LOW

    def test_ni_siquiera_cuando_el_monto_cierra_por_casualidad(self):
        """El canal giró el bruto entero: el residuo da cero. Que cierre no
        significa que se haya verificado de qué está hecho."""
        f = self._un_giro("NEQUI", "1000000", "1000000")
        assert f.confidence is Confidence.MEDIUM

    def test_el_informe_no_dice_que_todo_esta_explicado(self):
        from conciliacion.report.cfo import render_flow_report

        d, s = date(2026, 4, 13), date(2026, 4, 14)
        c = canal(
            venta("T1", d, "1000000", "D1", payment_method_type="NEQUI"),
            giro("D1", s, "900000", "D1"),
        )
        b = banco(credito("B1", s, "900000"))
        report = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026)

        assert report.unexplained_total() == Money.parse("100000")
        assert len(report.unverified) == 1
        texto = render_flow_report(report)
        assert "Todo el dinero del período está explicado" not in texto
        # Y no se disfraza de redondeo de la inferencia, que vale un centavo
        # por venta y se lee como ruido ignorable.
        assert "redondeo" not in texto

    def test_el_desglose_no_depende_del_orden_de_las_ventas(self):
        """El tarifario se resuelve por venta, no por `transactions[0]`.

        Ese orden es el `sha256` del id y no significa nada: el mismo desembolso
        salía con confianza baja o alta según cuál venta quedara primera.
        """
        d, s = date(2026, 4, 13), date(2026, 4, 14)

        def corrida(metodo_t1: str, metodo_t2: str):
            c = canal(
                venta("T1", d, "1000000", "D1", payment_method_type=metodo_t1),
                venta("T2", d, "1000000", "D1", payment_method_type=metodo_t2),
                giro("D1", s, "1900000", "D1"),
            )
            b = banco(credito("B1", s, "1900000"))
            (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).findings
            return f.confidence, f.explanation.unexplained, f.explanation.adjustments_total

        assert corrida("CARD", "NEQUI") == corrida("NEQUI", "CARD")

    def test_lo_que_no_se_pudo_calcular_se_nombra_aparte(self):
        """La parte con tarifario se desglosa; la otra queda como UNEXPLAINED.

        Repartir el faltante entre comisión e impuestos sería atribuirle a una
        regla conocida una plata cuya regla no conocemos.
        """
        d, s = date(2026, 4, 13), date(2026, 4, 14)
        c = canal(
            venta("T1", d, "1000000", "D1", payment_method_type="CARD"),
            venta("T2", d, "1000000", "D1", payment_method_type="NEQUI"),
            giro("D1", s, "1900000", "D1"),
        )
        b = banco(credito("B1", s, "1900000"))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).findings

        por_tipo = {a.kind: a.amount for a in f.explanation.adjustments}
        assert AdjustmentKind.UNEXPLAINED in por_tipo
        # La comisión se calcula solo sobre la venta con tarifario: 2,35% + $400.
        assert por_tipo[AdjustmentKind.COMMISSION] == Money.parse("23900")
        # Y la escalera cierra: lo desglosado + lo no calculable == el faltante.
        assert f.explanation.adjustments_total == Money.parse("100000")


class TestVentanaTemporal:
    def test_acredita_dias_habiles_despues(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 16), "100"))
        assert reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)

    def test_fuera_de_la_ventana_no_matchea(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 30), "100"))
        report = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026)
        assert report.of(FlowStatus.UNMATCHED_SETTLEMENT)

    def test_la_ventana_salta_fines_de_semana_y_festivos(self):
        """Giro el viernes 24/04; el banco acredita el lunes 27. Con días
        calendario la ventana [0,3] llegaría al lunes justo; con hábiles sobra."""
        c = canal(
            venta("T1", date(2026, 4, 23), "100", "D1"),
            giro("D1", date(2026, 4, 24), "100", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 27), "100"))
        assert reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)

    def test_la_cadencia_del_canal_se_respeta(self):
        """El POS liquida T+2: su ventana es distinta sin cambiar el motor."""
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 15), "100", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 20), "100"))
        angosta = SettlementPolicy(settlement_lag_business_days=2, max_business_days=1)
        ancha = SettlementPolicy(settlement_lag_business_days=2, max_business_days=5)
        assert not reconcile_flow(c, b, calendar=CAL, policy=angosta, coverage=TODO_2026).of(FlowStatus.MATCHED)
        assert reconcile_flow(c, b, calendar=CAL, policy=ancha, coverage=TODO_2026).of(FlowStatus.MATCHED)


class TestAmbiguedad:
    def test_dos_creditos_iguales_producen_AMBIGUOUS(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(
            credito("B1", date(2026, 4, 14), "100"),
            credito("B2", date(2026, 4, 15), "100"),
        )
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.AMBIGUOUS)
        assert f.confidence is Confidence.LOW

    def test_expone_las_alternativas_descartadas_con_motivo(self):
        """Exponer lo descartado es lo que convierte un match en un argumento."""
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(
            credito("B1", date(2026, 4, 14), "100"),
            credito("B2", date(2026, 4, 15), "100"),
        )
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.AMBIGUOUS)
        assert len(f.explanation.alternatives) == 1
        assert f.explanation.alternatives[0].rejected_because
        assert f.explanation.alternatives[0].movement_ids

    def test_un_credito_no_se_usa_dos_veces(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
            venta("T2", date(2026, 4, 13), "100", "D2"),
            giro("D2", date(2026, 4, 14), "100", "D2"),
        )
        b = banco(credito("B1", date(2026, 4, 14), "100"))
        report = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026)
        assert len(report.of(FlowStatus.MATCHED, FlowStatus.AMBIGUOUS)) == 1
        assert len(report.of(FlowStatus.UNMATCHED_SETTLEMENT)) == 1


class TestFaltaPlataVsFaltaData:
    """La distinción más importante del reporte: se ven idénticas y significan
    lo contrario."""

    def test_dentro_de_cobertura_es_faltante(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(credito("OTRO", date(2026, 4, 14), "999"))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.UNMATCHED_SETTLEMENT)
        assert f.status.is_problem
        assert f.explanation.unexplained == Money.parse("100")

    def test_fuera_de_cobertura_no_es_faltante(self):
        """Giro de mayo, extracto que termina en abril. No falta plata: falta
        el extracto."""
        c = canal(
            venta("T1", date(2026, 5, 3), "100", "D1"),
            giro("D1", date(2026, 5, 4), "100", "D1"),
        )
        b = banco(credito("B1", date(2026, 4, 14), "999"))
        hasta_abril = Coverage(
            channel=(date(2026, 1, 1), date(2026, 12, 31)),
            bank=(date(2026, 1, 1), date(2026, 4, 30)),
        )
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=hasta_abril).of(FlowStatus.OUT_OF_COVERAGE)
        assert not f.status.is_problem
        assert f.explanation.unexplained is None
        assert "datos" in f.explanation.summary

    def test_el_reporte_separa_problemas_de_notas_al_pie(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
            venta("T2", date(2026, 5, 3), "200", "D2"),
            giro("D2", date(2026, 5, 4), "200", "D2"),
        )
        # Ruido bancario ajeno al canal: no debe aparecer en el reporte.
        b = banco(credito("X", date(2026, 4, 20), "999", "ABONO INTERESES AHORROS"))
        hasta_abril = Coverage(
            channel=(date(2026, 1, 1), date(2026, 12, 31)),
            bank=(date(2026, 1, 1), date(2026, 4, 30)),
        )
        report = reconcile_flow(c, b, calendar=CAL, coverage=hasta_abril)

        # El giro de abril no aparece en el banco -> falta plata.
        assert len(report.problems) == 1
        assert report.problems[0].status is FlowStatus.UNMATCHED_SETTLEMENT
        # El de mayo cae fuera del extracto -> falta data, no es problema.
        assert len(report.of(FlowStatus.OUT_OF_COVERAGE)) == 1


class TestCreditosHuerfanos:
    def test_credito_del_canal_sin_giro(self):
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(
            credito("B1", date(2026, 4, 14), "100"),
            credito("B2", date(2026, 4, 15), "555", "PAGO DE PROV WOMPI S.A.S."),
        )
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.UNMATCHED_BANK)
        assert f.bank_amount == Money.parse("555")

    def test_el_ruido_bancario_no_se_reporta(self):
        """368 de 426 movimientos del extracto son ajenos al canal. Listarlos
        haría el reporte inservible."""
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(
            credito("B1", date(2026, 4, 14), "100"),
            credito("N1", date(2026, 4, 15), "999", "ABONO INTERESES AHORROS"),
            credito("N2", date(2026, 4, 16), "888", "PAGO INTERBANC DRUO SAS"),
        )
        report = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026)
        assert not report.of(FlowStatus.UNMATCHED_BANK)

    def test_la_deteccion_por_descripcion_se_declara_heuristica(self):
        """La descripción cambia a mitad del período; usarla como llave perdería
        53 de 58 líneas. El motor lo dice en la explicación."""
        c = canal(
            venta("T1", date(2026, 4, 13), "100", "D1"),
            giro("D1", date(2026, 4, 14), "100", "D1"),
        )
        b = banco(
            credito("B1", date(2026, 4, 14), "100"),
            credito("B2", date(2026, 4, 15), "555"),
        )
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.UNMATCHED_BANK)
        assert f.explanation.alternatives
        assert "heurística" in f.explanation.alternatives[0].rejected_because


class TestGiroSinVentas:
    def test_giro_cuyo_origen_esta_fuera_de_la_ventana(self):
        """El caso del desembolso 2800150: liquida ventas de diciembre 2025 que
        no ingerimos. Matchea contra el banco, pero no se puede explicar de qué
        está hecho."""
        c = canal(giro("D1", date(2026, 1, 2), "19715313.89", "D1"))
        b = banco(credito("B1", date(2026, 1, 2), "19715313.89", "PAGO DE PROV WOMPI S.A.S."))
        (f,) = reconcile_flow(c, b, calendar=CAL, coverage=TODO_2026).of(FlowStatus.MATCHED)
        assert f.transaction_ids == ()
        assert f.explanation.gross is None
        assert f.confidence is Confidence.MEDIUM
        assert "no hay ventas cargadas" in f.explanation.summary.lower()


# ── datos reales ────────────────────────────────────────────────────────────


class TestSobreLosDatosDelChallenge:
    """La corrida que importa: los ledgers reales, sin escenarios armados."""

    @pytest.fixture
    def ledgers(self):
        reg = SourceRegistry()
        reg.register_account(BANCOLOMBIA)
        reg.register_account(WOMPI)
        reg.register_source(SourceSpec(
            "banco", LocalFileConnector(FIXTURES / "bancolombia", "*.pdf"),
            (BancolombiaPdfAdapter(),),
        ))
        reg.register_source(SourceSpec(
            "wompi_csv", LocalFileConnector(FIXTURES / "wompi", "*.csv"),
            (WompiDisbursementCsvAdapter(),),
        ))
        banco_led, _ = ingest_all(reg, "bancolombia", strict=True)
        wompi_led, _ = ingest_all(reg, "wompi", strict=True)
        return wompi_led, banco_led

    def test_corre_sin_explotar(self, ledgers):
        canal_led, banco_led = ledgers
        report = reconcile_flow(canal_led, banco_led, calendar=CAL)
        assert report.channel_ledger_id == "wompi"
        assert report.coverage.bank is not None

    def test_todo_finding_tiene_explicacion_con_regla(self, ledgers):
        """Invariante del motor: no hay conclusión sin explicación."""
        canal_led, banco_led = ledgers
        for f in reconcile_flow(canal_led, banco_led, calendar=CAL).findings:
            assert f.explanation.rule_id
            assert f.explanation.summary
            assert f.confidence in set(Confidence)

    def test_los_creditos_wompi_de_abril_se_detectan(self, ledgers):
        """Sin la API solo tenemos los CSV, así que no hay giros: los 10
        créditos de Wompi de abril tienen que salir como huérfanos, no
        silenciarse."""
        canal_led, banco_led = ledgers
        report = reconcile_flow(canal_led, banco_led, calendar=CAL)
        huerfanos = report.of(FlowStatus.UNMATCHED_BANK, FlowStatus.OUT_OF_COVERAGE)
        montos = {f.bank_amount for f in huerfanos if f.bank_amount}
        for esperado in ("804163.63", "926373.11", "698160.78"):
            assert Money.parse(esperado) in montos

    def test_ningun_credito_ajeno_al_canal_se_reporta(self, ledgers):
        canal_led, banco_led = ledgers
        report = reconcile_flow(canal_led, banco_led, calendar=CAL)
        for f in report.of(FlowStatus.UNMATCHED_BANK):
            mov = banco_led.by_id(f.bank_movement_id)
            assert "WOMPI" in mov.description.upper()


class TestCoverage:
    def test_interseccion(self):
        c = Coverage(channel=(date(2026, 1, 1), date(2026, 5, 4)),
                     bank=(date(2026, 1, 1), date(2026, 4, 30)))
        assert c.overlap == (date(2026, 1, 1), date(2026, 4, 30))

    def test_sin_interseccion(self):
        c = Coverage(channel=(date(2026, 6, 1), date(2026, 6, 30)),
                     bank=(date(2026, 1, 1), date(2026, 4, 30)))
        assert c.overlap is None

    def test_sin_datos(self):
        assert Coverage(channel=None, bank=None).overlap is None
