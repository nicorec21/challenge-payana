"""Calendario de días hábiles de Colombia.

Necesario para T+1: si un pago cae un viernes, la acreditación no llega el
sábado. Y si el lunes es festivo, tampoco el lunes. Sin esto, todos los pagos
de fin de semana y los previos a festivo quedan sin conciliar, que en Colombia
son ~20 fines de semana largos por año.

Colombia aplica la Ley Emiliani: varios festivos se trasladan al lunes
siguiente. Está implementado, no aproximado, porque un festivo mal calculado
desplaza la ventana y produce un no-match que parece un problema de plata.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

#: Festivos de fecha fija que NO se trasladan.
_FIXED = (
    (1, 1),   # Año Nuevo
    (5, 1),   # Día del Trabajo
    (7, 20),  # Independencia
    (8, 7),   # Batalla de Boyacá
    (12, 8),  # Inmaculada Concepción
    (12, 25), # Navidad
)

#: Festivos de fecha fija que SÍ se trasladan al lunes siguiente (Ley Emiliani).
_EMILIANI_FIXED = (
    (1, 6),   # Reyes Magos
    (3, 19),  # San José
    (6, 29),  # San Pedro y San Pablo
    (8, 15),  # Asunción de la Virgen
    (10, 12), # Día de la Raza
    (11, 1),  # Todos los Santos
    (11, 11), # Independencia de Cartagena
)

#: Offsets desde el Domingo de Pascua. Los trasladables ya vienen con el
#: traslado al lunes aplicado en el offset.
_EASTER_OFFSETS = (
    -3,  # Jueves Santo (no se traslada)
    -2,  # Viernes Santo (no se traslada)
    43,  # Ascensión (Pascua+39, jueves → lunes)
    64,  # Corpus Christi (Pascua+60, jueves → lunes)
    71,  # Sagrado Corazón (Pascua+68, viernes → lunes)
)


def easter_sunday(year: int) -> date:
    """Domingo de Pascua (algoritmo gregoriano anónimo, Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    j = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * j) // 451
    month, day = divmod(h + j - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _next_monday(day: date) -> date:
    """El lunes siguiente, o el mismo día si ya es lunes."""
    return day + timedelta(days=(7 - day.weekday()) % 7)


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    """Festivos nacionales de Colombia para un año."""
    days = {date(year, month, day) for month, day in _FIXED}
    days |= {_next_monday(date(year, month, day)) for month, day in _EMILIANI_FIXED}
    easter = easter_sunday(year)
    days |= {easter + timedelta(days=offset) for offset in _EASTER_OFFSETS}
    return frozenset(days)


class BusinessCalendar:
    """Días hábiles. Configurable para poder testear y para adaptarse si una
    fuente resulta operar con otro calendario."""

    def __init__(self, country: str = "CO", extra_holidays: frozenset[date] = frozenset()) -> None:
        self.country = country
        self.extra_holidays = extra_holidays

    def is_holiday(self, day: date) -> bool:
        return day in self.extra_holidays or day in holidays(day.year)

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and not self.is_holiday(day)

    def next_business_day(self, day: date) -> date:
        """Primer hábil ESTRICTAMENTE posterior a `day`. Esto es T+1."""
        cursor = day + timedelta(days=1)
        while not self.is_business_day(cursor):
            cursor += timedelta(days=1)
        return cursor

    def shift(self, day: date, business_days: int) -> date:
        """Corre `business_days` hábiles. `0` devuelve el mismo día."""
        if business_days == 0:
            return day
        step = 1 if business_days > 0 else -1
        cursor = day
        for _ in range(abs(business_days)):
            cursor += timedelta(days=step)
            while not self.is_business_day(cursor):
                cursor += timedelta(days=step)
        return cursor

    def business_days_between(self, start: date, end: date) -> int:
        """Hábiles en `(start, end]`. Negativo si `end < start`."""
        if end == start:
            return 0
        step = 1 if end > start else -1
        count, cursor = 0, start
        while cursor != end:
            cursor += timedelta(days=step)
            if self.is_business_day(cursor):
                count += step
        return count
