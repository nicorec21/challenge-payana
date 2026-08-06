from decimal import Decimal

import pytest

from conciliacion.domain import Money


class TestParseo:
    """Los formatos que aparecen en extractos bancarios y APIs reales."""

    @pytest.mark.parametrize(
        "raw,expected_minor",
        [
            ("1234", 123_400),
            ("1.234.567,89", 123_456_789),   # formato CO/ES
            ("1,234,567.89", 123_456_789),   # formato US
            ("$ 1.234.567", 123_456_700),
            ("1234,50", 123_450),
            ("1234.50", 123_450),
            ("1.234", 123_400),              # 3 dígitos atrás => miles, no decimal
            ("1,234", 123_400),
            ("0", 0),
        ],
    )
    def test_formatos_positivos(self, raw, expected_minor):
        assert Money.parse(raw).amount == expected_minor

    @pytest.mark.parametrize(
        "raw",
        ["-1.234,56", "(1.234,56)", "1.234,56-"],  # menos, contable, mainframe
    )
    def test_notaciones_de_negativo(self, raw):
        assert Money.parse(raw) == Money(-123_456)

    def test_numeros_nativos(self):
        assert Money.parse(1234).amount == 123_400
        assert Money.parse(Decimal("1234.56")).amount == 123_456

    def test_vacio_falla(self):
        with pytest.raises(ValueError):
            Money.parse("   ")


class TestAritmetica:
    def test_suma_y_resta(self):
        assert Money(100) + Money(250) == Money(350)
        assert Money(100) - Money(250) == Money(-150)

    def test_sum_de_lista_vacia_es_cero(self):
        assert Money.sum([]) == Money.zero()

    def test_monedas_distintas_no_se_mezclan(self):
        with pytest.raises(ValueError):
            Money(100, "COP") + Money(100, "USD")

    def test_float_rechazado_en_constructor(self):
        """El punto de Money: que sea imposible meter un float por accidente."""
        with pytest.raises(TypeError):
            Money(100.5)  # type: ignore[arg-type]

    def test_sin_perdida_de_precision_al_acumular(self):
        """Caso que rompería con float: 1000 sumas de 0.01."""
        total = Money.sum([Money(1)] * 1000)
        assert total == Money(1000)
        assert total.units == Decimal("10.00")


class TestRatio:
    def test_comision_como_fraccion_del_bruto(self):
        comision, bruto = Money(1_000), Money(45_000)
        assert comision.ratio_to(bruto) == pytest.approx(Decimal("0.0222"), abs=1e-4)

    def test_ratio_sobre_cero_falla(self):
        with pytest.raises(ZeroDivisionError):
            Money(100).ratio_to(Money.zero())


def test_formato_colombiano():
    assert Money.parse("1234567,89").format() == "$1.234.567,89 COP"
    assert Money.parse("-500").format() == "-$500,00 COP"
