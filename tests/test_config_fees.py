"""El tarifario derivado, contrastado contra las 9 filas reales de la muestra.

Estos tests no validan código: validan que la derivación empírica sigue siendo
correcta. Si Wompi cambia la tarifa, fallan y avisan.
"""

from datetime import date

import pytest

from conciliacion.config import fee_schedule_for
from conciliacion.domain import Money

DAY = date(2026, 4, 15)

#: (monto, comisión, iva_comisión, retefuente, total_desembolsado)
#: Transcritas de data/raw/wompi/*.csv
MUESTRA = [
    ("840763.00", "20157.93", "3830.00", "12611.44", "804163.63"),
    ("117729.00", "3166.63", "601.65", "1765.93", "112194.79"),
    ("339282.00", "8373.12", "1590.89", "5089.23", "324228.76"),
    ("283765.00", "7068.47", "1343.01", "4256.47", "271097.05"),
    ("229175.00", "5785.61", "1099.26", "3437.62", "218852.51"),
    ("154608.00", "4033.28", "766.32", "2319.12", "147489.28"),
    ("332690.00", "8218.21", "1561.46", "4990.35", "317919.98"),
    ("243698.00", "6126.90", "1164.11", "3655.47", "232751.52"),
    ("317549.00", "7862.40", "1493.85", "4763.23", "303429.52"),
]


@pytest.fixture
def card():
    schedule = fee_schedule_for("CARD", DAY)
    assert schedule is not None
    return schedule


@pytest.mark.parametrize("gross,commission,iva,retefuente,net", MUESTRA)
class TestTarifarioContraDataReal:
    def test_comision(self, card, gross, commission, iva, retefuente, net):
        assert card.commission(Money.parse(gross)) == Money.parse(commission)

    def test_iva_sobre_comision_sin_truncar(self, card, gross, commission, iva, retefuente, net):
        """Calcular el IVA sobre la comisión ya truncada falla por un centavo
        en 2 de estas 9 filas."""
        assert card.iva(Money.parse(gross)) == Money.parse(iva)

    def test_retefuente(self, card, gross, commission, iva, retefuente, net):
        assert card.retefuente(Money.parse(gross)) == Money.parse(retefuente)

    def test_neto_esperado(self, card, gross, commission, iva, retefuente, net):
        assert card.expected_net(Money.parse(gross)) == Money.parse(net)

    def test_identidad_del_csv(self, card, gross, commission, iva, retefuente, net):
        """El invariante que toda fila ingerida debe cumplir, independiente
        del tarifario: neto = bruto − descuentos declarados."""
        calculado = (
            Money.parse(gross)
            - Money.parse(commission)
            - Money.parse(iva)
            - Money.parse(retefuente)
        )
        assert calculado == Money.parse(net)


class TestTrunca:
    def test_trunca_no_redondea(self, card):
        """283765 × 0.0235 + 400 = 7068.4775 → 7068.47, no 7068.48."""
        assert card.commission(Money.parse("283765")) == Money.parse("7068.47")

    def test_componente_fijo_existe(self, card):
        """Sin los $400 fijos, la comisión de un monto chico se va lejos.
        Es lo que hacía que comisión/monto no diera un porcentaje redondo."""
        assert card.commission(Money.zero()) == Money.parse("400")


class TestMedioDeCobroDesconocido:
    def test_medio_desconocido_devuelve_none(self):
        """`None` no es error: es 'no puedo validar esta fila'. El movimiento
        se ingiere igual con sus montos declarados."""
        assert fee_schedule_for("NEQUI", DAY) is None

    def test_fecha_fuera_de_vigencia(self):
        assert fee_schedule_for("CARD", date(2020, 1, 1)) is None
