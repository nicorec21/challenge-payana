from datetime import date

import pytest

from conciliacion.domain import Account, Ledger, Money, Movement, MovementKind, MovementStatus


def mov(external_id: str, day: int, amount: int, *, ledger="wompi", **kw) -> Movement:
    return Movement(
        ledger_id=ledger,
        source_id=kw.pop("source_id", "wompi_api"),
        external_id=external_id,
        occurred_on=date(2025, 3, day),
        amount=Money(amount),
        kind=kw.pop("kind", MovementKind.PAYMENT),
        **kw,
    )


@pytest.fixture
def ledger():
    return Ledger(Account("wompi", "Cuenta Wompi", role="channel"))


class TestIdentidad:
    def test_id_es_determinista(self):
        """Dos corridas sobre la misma data dan el mismo ID: los reportes son
        diffeables y las explicaciones citables entre corridas."""
        assert mov("TX-1", 10, 100).id == mov("TX-1", 10, 100).id

    def test_id_no_depende_de_campos_mutables(self):
        """Solo (source_id, external_id) determinan el ID. Si el monto cambia
        pero el ID de la fuente es el mismo, es el mismo hecho corregido."""
        assert mov("TX-1", 10, 100).id == mov("TX-1", 11, 999).id

    def test_ids_distintos_para_externals_distintos(self):
        assert mov("TX-1", 10, 100).id != mov("TX-2", 10, 100).id

    def test_external_id_obligatorio(self):
        with pytest.raises(ValueError):
            mov("", 10, 100)


class TestIdempotencia:
    def test_reingerir_no_duplica(self, ledger):
        assert ledger.add(mov("TX-1", 10, 100)) is True
        assert ledger.add(mov("TX-1", 10, 100)) is False
        assert len(ledger) == 1

    def test_dos_pagos_iguales_el_mismo_dia_son_dos_movimientos(self, ledger):
        """Dedup por clave de la fuente, NO por contenido: dos comensales
        pueden pagar $100 el mismo día."""
        ledger.add(mov("TX-1", 10, 10_000))
        ledger.add(mov("TX-2", 10, 10_000))
        assert len(ledger) == 2

    def test_misma_external_id_de_fuentes_distintas_coexiste(self, ledger):
        ledger.add(mov("1", 10, 100, source_id="wompi_api"))
        ledger.add(mov("1", 10, 100, source_id="wompi_webhook"))
        assert len(ledger) == 2

    def test_extend_devuelve_cuantos_eran_nuevos(self, ledger):
        assert ledger.extend([mov("TX-1", 10, 100), mov("TX-1", 10, 100), mov("TX-2", 10, 200)]) == 2


class TestInvariantes:
    def test_rechaza_movimiento_de_otro_ledger(self, ledger):
        with pytest.raises(ValueError, match="ledger"):
            ledger.add(mov("TX-1", 10, 100, ledger="bancolombia"))

    def test_rechaza_moneda_distinta(self, ledger):
        m = Movement("wompi", "s", "TX-1", date(2025, 3, 10), Money(100, "USD"), MovementKind.PAYMENT)
        with pytest.raises(ValueError, match="[Mm]oneda"):
            ledger.add(m)


class TestConsultas:
    @pytest.fixture
    def poblado(self, ledger):
        ledger.extend([
            mov("TX-1", 14, 100_00),
            mov("TX-2", 14, 200_00),
            mov("TX-3", 15, 150_00),
            mov("FEE-1", 14, -10_00, kind=MovementKind.FEE),
            mov("TX-4", 14, 999_00, status=MovementStatus.DECLINED),
        ])
        return ledger

    def test_orden_cronologico_estable(self, poblado):
        days = [m.occurred_on for m in poblado]
        assert days == sorted(days)

    def test_orden_no_depende_del_orden_de_insercion(self):
        """Mismo set, insertado al revés, mismo reporte."""
        movs = [mov("A", 14, 100), mov("B", 14, 200), mov("C", 15, 300)]
        a, b = Ledger(Account("wompi", "W")), Ledger(Account("wompi", "W"))
        a.extend(movs)
        b.extend(reversed(movs))
        assert [m.id for m in a] == [m.id for m in b]

    def test_balance_es_suma_con_signo(self, poblado):
        assert poblado.balance() == Money(100_00 + 200_00 + 150_00 - 10_00 + 999_00)

    def test_balance_hasta_fecha(self, poblado):
        assert poblado.balance(up_to=date(2025, 3, 14)) == Money(100_00 + 200_00 - 10_00 + 999_00)

    def test_declinados_se_ingieren_pero_no_concilian(self, poblado):
        """Un pago rechazado tiene que estar en el ledger: explicar por qué NO
        llegó al banco requiere tenerlo."""
        assert len(poblado) == 5
        assert len(poblado.approved()) == 4

    def test_between_es_inclusivo(self, poblado):
        assert len(poblado.between(date(2025, 3, 14), date(2025, 3, 14))) == 4

    def test_group_by_day(self, poblado):
        grupos = poblado.group_by_day()
        assert set(grupos) == {date(2025, 3, 14), date(2025, 3, 15)}
        assert len(grupos[date(2025, 3, 14)]) == 4

    def test_date_range_vacio_es_none(self, ledger):
        assert ledger.date_range is None
