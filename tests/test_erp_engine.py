"""Conciliación contra el libro contable del ERP.

El enunciado pide, línea por línea: coincidencias con **los dos identificadores**
y discrepancias explícitas con su porqué. Estos tests fijan eso.

Sin red: los payloads de Odoo son diccionarios, así que el adapter se ejercita
con fixtures en memoria igual que cualquier otro.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conciliacion.domain import (
    Account,
    Confidence,
    Ledger,
    Money,
    Movement,
    MovementKind,
    MovementStatus,
)
from conciliacion.ingest.adapters.odoo_ledger import OdooLedgerAdapter
from conciliacion.ingest.ports import IngestionError, RawRecord
from conciliacion.reconcile.erp.engine import reconcile_erp
from conciliacion.reconcile.erp.findings import ErpStatus

# ── fixtures de datos ───────────────────────────────────────────────────────


def linea(id_, fecha, debit=0.0, credit=0.0, ref=None, state="posted", move="WMP/2026/00001"):
    """Una `account.move.line` con la forma real que devuelve Odoo."""
    return {
        "id": id_,
        "date": fecha,
        "ref": ref,
        "name": ref or "",
        "debit": debit,
        "credit": credit,
        "account_id": [919, "1110001 Wompi Tarjetas"],
        "journal_id": [48, "Wompi Tarjetas"],
        "move_id": [1303, move],
        "move_name": move,
        "parent_state": state,
    }


def record(payload, account="1110001"):
    return RawRecord(
        locator=f"data/raw/odoo/{account}/lines-p001.json#line={payload['id']}",
        payload=payload,
        metadata={"account_code": account, "redacted": True},
    )


def libro(*payloads, account="1110001") -> Ledger:
    led = Ledger(Account("wompi_erp", "Libro Odoo", role="erp"))
    adapter = OdooLedgerAdapter("wompi_erp", account)
    led.extend(m for p in payloads for m in adapter.parse(record(p, account)))
    return led


def ledger(*movs) -> Ledger:
    led = Ledger(Account("wompi", "Canal", role="channel"))
    led.extend(movs)
    return led


def mov(ext, fecha, monto, kind=MovementKind.PAYMENT, ref=None, **kw):
    return Movement(
        ledger_id="wompi", source_id="api", external_id=ext, occurred_on=fecha,
        amount=Money.parse(monto), kind=kind, reference=ref,
        status=kw.pop("status", MovementStatus.APPROVED), **kw,
    )


# ── adapter ─────────────────────────────────────────────────────────────────


class TestAdapterDeOdoo:
    def test_debe_menos_haber(self):
        """La proyección de partida doble a simple: una línea, sin casos
        especiales. Ambas cuentas son `asset_cash`, así que debe = entrada."""
        (venta,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(1, "2026-04-14", debit=243698.0, ref="ABC"))
        )
        (giro,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(2, "2026-04-01", credit=1327369.53, ref="Acreditación Wompi"))
        )
        assert venta.amount == Money.parse("243698")
        assert giro.amount == Money.parse("-1327369.53")

    def test_no_clasifica(self):
        """Inferir el tipo económico del signo sería la clasificación que los
        adapters no hacen. El signo ya lleva la dirección."""
        movs = [m for p in (linea(1, "2026-04-14", debit=100.0),
                            linea(2, "2026-04-15", credit=100.0))
                for m in OdooLedgerAdapter("wompi_erp", "1110001").parse(record(p))]
        assert {m.kind for m in movs} == {MovementKind.OTHER}

    @pytest.mark.parametrize(
        "state,esperado",
        [("posted", MovementStatus.APPROVED),
         ("draft", MovementStatus.PENDING),
         ("cancel", MovementStatus.VOIDED)],
    )
    def test_mapea_el_estado_del_asiento(self, state, esperado):
        (m,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(1, "2026-04-14", debit=100.0, state=state))
        )
        assert m.status is esperado

    def test_los_no_confirmados_se_ingieren_igual(self):
        """Descartarlos en ingesta impediría reportar «el ERP tiene el asiento
        pero sin confirmar», que no es ni coincidencia ni ausencia."""
        led = libro(linea(1, "2026-04-14", debit=100.0, state="draft"))
        assert len(led) == 1
        assert led.balance() == Money.zero()  # pendiente no suma al saldo

    def test_el_id_de_la_linea_es_la_clave(self):
        """Odoo da un id único y estable: no hace falta clave sintética como en
        el extracto en PDF."""
        (m,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(4242, "2026-04-14", debit=100.0))
        )
        assert m.external_id == "4242"

    def test_conserva_el_detalle_contable(self):
        (m,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(1, "2026-04-14", debit=243698.0, ref="ABC"))
        )
        assert m.metadata["move_name"] == "WMP/2026/00001"
        assert m.metadata["journal"] == "Wompi Tarjetas"
        # Odoo devuelve floats; el detalle se guarda como texto para poder
        # auditar exactamente lo que dijo la fuente, sin reinterpretarlo.
        assert m.metadata["debit"] == "243698.0"
        assert m.metadata["credit"] == "0"

    def test_no_usa_float_para_el_monto(self):
        """Odoo devuelve floats; convertirlos vía Decimal evita que el error de
        redondeo se confunda con una diferencia contable real."""
        (m,) = OdooLedgerAdapter("wompi_erp", "1110001").parse(
            record(linea(1, "2026-04-14", debit=0.1 + 0.2))
        )
        assert m.amount == Money.parse("0.30")

    def test_linea_sin_fecha_falla_con_contexto(self):
        rota = linea(1, "2026-04-14", debit=100.0)
        del rota["date"]
        with pytest.raises(IngestionError, match="mal formada"):
            list(OdooLedgerAdapter("wompi_erp", "1110001").parse(record(rota)))

    def test_sniff(self):
        adapter = OdooLedgerAdapter("wompi_erp", "1110001")
        assert adapter.sniff(record(linea(1, "2026-04-14", debit=1.0)))
        assert not adapter.sniff(record(linea(1, "2026-04-14", debit=1.0), account="111001"))


# ── conciliación ────────────────────────────────────────────────────────────


class TestCoincidencias:
    def test_por_referencia_trae_los_dos_ids(self):
        """El enunciado: «mostrar el dato con el ID del Ledger y el ID del
        LibroContable, dejando claro que representan la misma cosa»."""
        led = ledger(mov("T1", date(2026, 4, 14), "243698", ref="tkfgjokoqfhwvigu71qqq"))
        book = libro(linea(9, "2026-04-14", debit=243698.0, ref="TKFGJOKOQFHWVIGU71QQQ"))
        (f,) = reconcile_erp(led, book).of(ErpStatus.MATCHED)

        assert f.ledger_movement_id
        assert f.book_movement_id
        assert f.erp_line_id == "9"
        assert f.erp_move_name == "WMP/2026/00001"
        assert f.confidence is Confidence.EXACT

    def test_la_referencia_matchea_ignorando_mayusculas(self):
        """El ERP las guarda en mayúsculas y el canal en minúsculas."""
        led = ledger(mov("T1", date(2026, 4, 14), "100", ref="abc123"))
        book = libro(linea(9, "2026-04-14", debit=100.0, ref="ABC123"))
        assert reconcile_erp(led, book).of(ErpStatus.MATCHED)

    def test_por_monto_y_fecha_cuando_la_referencia_no_sirve(self):
        """`Acreditación Wompi` se repite en todas las líneas del diario
        bancario: no identifica ninguna."""
        led = ledger(mov("S1", date(2026, 4, 1), "-1327369.53", kind=MovementKind.SETTLEMENT))
        book = libro(
            linea(1, "2026-04-01", credit=1327369.53, ref="Acreditación Wompi", move="BNK/1"),
            linea(2, "2026-04-06", credit=257940.85, ref="Acreditación Wompi", move="BNK/2"),
        )
        report = reconcile_erp(led, book)
        (f,) = report.of(ErpStatus.MATCHED)
        assert f.explanation.rule_id == "erp.matched_by_amount_and_date"
        assert "no identifica una sola línea" in f.explanation.summary

    def test_tolera_diferencia_de_fecha(self):
        """El asiento puede llevar la fecha del hecho o la de registración."""
        led = ledger(mov("S1", date(2026, 4, 1), "-100", kind=MovementKind.SETTLEMENT))
        book = libro(linea(1, "2026-04-03", credit=100.0, ref="X"))
        assert reconcile_erp(led, book).of(ErpStatus.MATCHED)

    def test_fuera_de_tolerancia_no_matchea(self):
        led = ledger(mov("S1", date(2026, 4, 1), "-100", kind=MovementKind.SETTLEMENT))
        book = libro(linea(1, "2026-04-20", credit=100.0, ref="X"))
        report = reconcile_erp(led, book, date_tolerance=timedelta(days=3))
        assert report.of(ErpStatus.MISSING_IN_ERP)
        assert report.of(ErpStatus.MISSING_IN_LEDGER)

    def test_una_linea_no_se_usa_dos_veces(self):
        led = ledger(
            mov("T1", date(2026, 4, 14), "100"),
            mov("T2", date(2026, 4, 14), "100"),
        )
        book = libro(linea(1, "2026-04-14", debit=100.0, ref="X"))
        report = reconcile_erp(led, book)
        assert len(report.of(ErpStatus.MATCHED)) == 1
        assert len(report.of(ErpStatus.MISSING_IN_ERP)) == 1


class TestLaReferenciaApuntaAUnaTransaccionNoAUnMovimiento:
    """El bug que costó reescribir el matcher.

    En el ledger una transacción son hasta cinco movimientos (pago, comisión,
    IVA, retención, giro) y **todos comparten la referencia**. En el libro es
    una sola línea. Recorriendo el ledger, la comisión llegaba primero y se
    llevaba la línea de la venta, produciendo un `AMOUNT_MISMATCH` falso.
    """

    @pytest.fixture
    def escenario(self):
        ref = "tkfgjokoqfhwvigu71qqq"
        led = ledger(
            mov("T1", date(2026, 4, 14), "243698", ref=ref),
            mov("T1:comisión", date(2026, 4, 14), "-6126.90", MovementKind.FEE, ref=ref),
            mov("T1:iva", date(2026, 4, 14), "-1164.11", MovementKind.TAX, ref=ref),
        )
        book = libro(linea(9, "2026-04-14", debit=243698.0, ref="TKFGJOKOQFHWVIGU71QQQ"))
        return reconcile_erp(led, book)

    def test_la_linea_se_asigna_al_pago_no_a_la_comision(self, escenario):
        (f,) = escenario.of(ErpStatus.MATCHED)
        assert f.kind == "payment"
        assert f.ledger_amount == Money.parse("243698")

    def test_no_inventa_una_diferencia_de_monto(self, escenario):
        assert not escenario.of(ErpStatus.AMOUNT_MISMATCH)

    def test_la_comision_queda_como_faltante(self, escenario):
        """Y eso es correcto: el ERP no registra comisiones."""
        faltantes = {f.kind for f in escenario.of(ErpStatus.MISSING_IN_ERP)}
        assert faltantes == {"fee", "tax"}


class TestDiscrepancias:
    def test_diferencia_de_monto_con_la_misma_referencia(self):
        """El caso más grave: el ERP registró el hecho con otro número."""
        led = ledger(mov("T1", date(2026, 4, 14), "243698", ref="abc"))
        book = libro(linea(9, "2026-04-14", debit=200000.0, ref="ABC"))
        (f,) = reconcile_erp(led, book).of(ErpStatus.AMOUNT_MISMATCH)

        assert f.ledger_amount == Money.parse("243698")
        assert f.book_amount == Money.parse("200000")
        assert f.difference == Money.parse("-43698")
        assert f.explanation.unexplained == Money.parse("-43698")
        assert f.status.is_problem

    def test_movimiento_sin_asiento(self):
        led = ledger(mov("T1", date(2026, 4, 14), "100", ref="abc"))
        (f,) = reconcile_erp(led, libro()).of(ErpStatus.MISSING_IN_ERP)
        assert f.ledger_movement_id
        assert f.book_movement_id is None
        assert f.explanation.unexplained == Money.parse("100")

    def test_asiento_sin_movimiento(self):
        """La dirección inversa: un ERP que registra algo que no pasó es tan
        problema como uno al que le falta un registro."""
        book = libro(linea(9, "2026-04-14", debit=100.0, ref="X", move="BNK8/2026/00002"))
        (f,) = reconcile_erp(ledger(), book).of(ErpStatus.MISSING_IN_LEDGER)
        assert f.erp_move_name == "BNK8/2026/00002"
        assert f.ledger_movement_id is None

    def test_asiento_sin_confirmar(self):
        book = libro(linea(9, "2026-04-14", debit=100.0, state="draft", move="DRAFT/1"))
        (f,) = reconcile_erp(ledger(), book).of(ErpStatus.NOT_POSTED)
        assert "no forma parte del libro formal" in f.explanation.summary
        assert f.status.is_problem

    def test_los_anulados_tampoco_se_comparan(self):
        led = ledger(mov("T1", date(2026, 4, 14), "100", ref="abc"))
        book = libro(linea(9, "2026-04-14", debit=100.0, ref="ABC", state="cancel"))
        report = reconcile_erp(led, book)
        assert report.of(ErpStatus.NOT_POSTED)
        assert report.of(ErpStatus.MISSING_IN_ERP)   # el movimiento sigue sin respaldo
        assert not report.of(ErpStatus.MATCHED)


class TestReporte:
    def test_todo_finding_tiene_explicacion(self):
        led = ledger(
            mov("T1", date(2026, 4, 14), "100", ref="abc"),
            mov("T2", date(2026, 4, 15), "200"),
        )
        book = libro(
            linea(1, "2026-04-14", debit=100.0, ref="ABC"),
            linea(2, "2026-04-20", debit=999.0, ref="Z", move="OTRO/1"),
        )
        for f in reconcile_erp(led, book).findings:
            assert f.explanation.rule_id
            assert f.explanation.summary

    def test_agrupa_faltantes_por_tipo(self):
        """«El ERP no registra comisiones» es una conclusión; 27 findings de
        comisión suelta son ruido con la misma información."""
        led = ledger(
            mov("T1", date(2026, 4, 14), "-10", MovementKind.FEE),
            mov("T2", date(2026, 4, 14), "-20", MovementKind.FEE),
            mov("T3", date(2026, 4, 14), "500"),
        )
        grupos = reconcile_erp(led, libro()).by_kind(ErpStatus.MISSING_IN_ERP)
        assert grupos["fee"]["count"] == 2
        assert grupos["fee"]["total"] == Money.parse("-30")
        assert grupos["payment"]["count"] == 1

    def test_cobertura_del_erp(self):
        led = ledger(
            mov("T1", date(2026, 4, 14), "100", ref="a"),
            mov("T2", date(2026, 4, 14), "200", ref="b"),
            mov("T3", date(2026, 4, 14), "300", ref="c"),
            mov("T4", date(2026, 4, 14), "400", ref="d"),
        )
        book = libro(linea(1, "2026-04-14", debit=100.0, ref="A"))
        assert reconcile_erp(led, book).coverage_ratio() == 0.25

    def test_la_cobertura_separa_lo_que_no_tiene_cuenta_donde_asentarse(self):
        """Un solo porcentaje junta dos problemas que se arreglan al revés.

        «Falta el asiento» se arregla asentando; «no existe la cuenta donde
        asentarlo» se arregla rediseñando el plan. Un número que baja por las
        dos razones no le dice a nadie qué hacer.
        """
        led = ledger(
            mov("T1", date(2026, 4, 14), "100", ref="a"),   # asentada
            mov("T2", date(2026, 4, 14), "200", ref="b"),   # falta el asiento
            mov("F1", date(2026, 4, 14), "-10", MovementKind.FEE),
            mov("F2", date(2026, 4, 14), "-20", MovementKind.TAX),
        )
        book = libro(linea(1, "2026-04-14", debit=100.0, ref="A"))
        cov = reconcile_erp(led, book).coverage()

        assert cov["comparable"] == 2 and cov["matched"] == 1
        assert cov["ratio"] == 0.5           # sobre lo que el plan puede representar
        assert cov["overall_ratio"] == 0.25  # el global, que mezcla las dos causas
        assert cov["unrepresentable"] == 2
        assert cov["unrepresentable_total"] == Money.parse("-30")
        assert cov["comparable"] + cov["unrepresentable"] == 4

    def test_no_se_excluye_un_tipo_solo_porque_dio_cero(self):
        """La exclusión sale de `config`, no de contar ceros.

        Que un tipo no tenga ninguna coincidencia puede ser casualidad. Que
        **no exista la cuenta** es un hecho del plan contable: afirmarlo
        requiere haberlo mirado, no inferirlo del resultado.
        """
        led = ledger(
            mov("S1", date(2026, 4, 14), "-100", MovementKind.SETTLEMENT),
            mov("S2", date(2026, 4, 14), "-200", MovementKind.SETTLEMENT),
        )
        cov = reconcile_erp(led, libro()).coverage()

        assert cov["unrepresentable"] == 0
        assert cov["comparable"] == 2
        assert cov["ratio"] == 0.0  # cero de verdad: faltan los asientos

    def test_los_rechazados_del_ledger_no_se_comparan(self):
        """Una venta rechazada no debería estar en el libro contable."""
        led = ledger(
            mov("T1", date(2026, 4, 14), "100", status=MovementStatus.DECLINED),
        )
        assert reconcile_erp(led, libro()).findings == []
