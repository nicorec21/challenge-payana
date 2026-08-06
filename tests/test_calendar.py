from datetime import date

import pytest

from conciliacion.reconcile.calendar import BusinessCalendar, easter_sunday, holidays


@pytest.fixture
def cal():
    return BusinessCalendar("CO")


class TestPascua:
    @pytest.mark.parametrize(
        "year,expected",
        [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))],
    )
    def test_domingo_de_pascua(self, year, expected):
        assert easter_sunday(year) == expected


class TestFestivosColombia:
    def test_fijos_no_se_trasladan(self):
        h = holidays(2025)
        assert date(2025, 1, 1) in h
        assert date(2025, 7, 20) in h   # cae domingo y NO se traslada
        assert date(2025, 7, 21) not in h

    def test_ley_emiliani_traslada_al_lunes(self):
        """Reyes 2025 cae lunes 6 (ya es lunes). En 2026 cae martes 6 → lunes 12."""
        assert date(2025, 1, 6) in holidays(2025)
        assert date(2026, 1, 12) in holidays(2026)
        assert date(2026, 1, 6) not in holidays(2026)

    def test_semana_santa_no_se_traslada(self):
        h = holidays(2025)
        assert date(2025, 4, 17) in h  # Jueves Santo
        assert date(2025, 4, 18) in h  # Viernes Santo

    def test_moviles_trasladados(self):
        h = holidays(2025)
        assert date(2025, 6, 2) in h   # Ascensión
        assert date(2025, 6, 23) in h  # Corpus Christi
        assert date(2025, 6, 30) in h  # Sagrado Corazón

    def test_cantidad_anual(self):
        """Colombia tiene 18 festivos nacionales en un año normal."""
        assert len(holidays(2024)) == 18
        assert len(holidays(2026)) == 18

    def test_2025_tiene_17_por_colision(self):
        """Caso borde real: en 2025, San Pedro y San Pablo (29/6 domingo, se
        traslada al lunes 30) cae el mismo día que Sagrado Corazón
        (Pascua+71 = 30/6). Dos festivos, una sola fecha inhábil.

        Importa porque el calendario se usa para T+1: contar 30/6 dos veces no
        rompe nada, pero asumir 18 fechas distintas sí rompería un test de
        cobertura anual."""
        assert date(2025, 6, 30) in holidays(2025)
        assert len(holidays(2025)) == 17


class TestDiasHabiles:
    def test_fin_de_semana_no_es_habil(self, cal):
        assert not cal.is_business_day(date(2025, 3, 15))  # sábado
        assert not cal.is_business_day(date(2025, 3, 16))  # domingo
        assert cal.is_business_day(date(2025, 3, 17))      # lunes

    def test_t1_de_viernes_salta_a_lunes(self, cal):
        """El caso que rompe la conciliación si no se maneja: los pagos del
        viernes se acreditan el lunes, no el sábado."""
        assert cal.next_business_day(date(2025, 3, 14)) == date(2025, 3, 17)

    def test_t1_antes_de_puente_salta_el_festivo(self, cal):
        """Viernes 2025-06-27 → Sagrado Corazón lunes 30 → martes 1 de julio."""
        assert cal.next_business_day(date(2025, 6, 27)) == date(2025, 7, 1)

    def test_shift_cero_es_identidad(self, cal):
        assert cal.shift(date(2025, 3, 14), 0) == date(2025, 3, 14)

    def test_shift_negativo(self, cal):
        assert cal.shift(date(2025, 3, 17), -1) == date(2025, 3, 14)

    def test_business_days_between_ignora_fin_de_semana(self, cal):
        assert cal.business_days_between(date(2025, 3, 14), date(2025, 3, 17)) == 1
        assert cal.business_days_between(date(2025, 3, 17), date(2025, 3, 21)) == 4

    def test_feriado_extra_configurable(self):
        """Si una fuente resulta operar con otro calendario, se inyecta."""
        cal = BusinessCalendar("CO", extra_holidays=frozenset({date(2025, 3, 17)}))
        assert cal.next_business_day(date(2025, 3, 14)) == date(2025, 3, 18)
