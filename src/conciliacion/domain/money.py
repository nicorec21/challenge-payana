"""Money como entero en unidades menores.

Nunca float. La conciliación compara montos por igualdad y acumula sumas de
cientos de movimientos; con float los errores de redondeo se vuelven
indistinguibles de las comisiones reales que justamente queremos inferir.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Unidades menores por unidad de moneda. COP se opera en pesos enteros pero
#: el ERP puede exponer decimales, así que guardamos centavos.
MINOR_UNITS = {"COP": 2, "USD": 2}


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """Monto con signo. `amount` está en unidades menores (centavos)."""

    amount: int
    currency: str = "COP"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError(f"Money.amount debe ser int, no {type(self.amount).__name__}")
        if self.currency not in MINOR_UNITS:
            raise ValueError(f"Moneda no soportada: {self.currency}")

    # -- constructores ----------------------------------------------------

    @classmethod
    def zero(cls, currency: str = "COP") -> Money:
        return cls(0, currency)

    @classmethod
    def parse(cls, raw: str | int | float | Decimal, currency: str = "COP") -> Money:
        """Parsea un monto desde texto de fuente externa.

        Tolera los formatos que aparecen en extractos y APIs:
        `"1.234.567,89"`, `"1,234,567.89"`, `"$ 1234567"`, `"(1.234)"` (negativo
        contable), `"1234567-"` (signo al final, típico de mainframe bancario).
        """
        if isinstance(raw, Money):  # type: ignore[unreachable]
            return raw
        if isinstance(raw, int) and not isinstance(raw, bool):
            return cls.from_units(Decimal(raw), currency)
        if isinstance(raw, (float, Decimal)):
            return cls.from_units(Decimal(str(raw)), currency)

        text = str(raw).strip()
        if not text:
            raise ValueError("Monto vacío")

        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative, text = True, text[1:-1]
        if text.endswith("-"):
            negative, text = True, text[:-1]
        if text.startswith("-"):
            negative, text = True, text[1:]
        if text.startswith("+"):
            text = text[1:]

        text = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
        text = _normalize_separators(text)
        if not text:
            raise ValueError(f"No se pudo parsear monto: {raw!r}")

        try:
            value = Decimal(text)
        except InvalidOperation as exc:  # pragma: no cover - defensivo
            raise ValueError(f"No se pudo parsear monto: {raw!r}") from exc

        return cls.from_units(-value if negative else value, currency)

    @classmethod
    def from_units(cls, value: Decimal, currency: str = "COP") -> Money:
        """Desde unidades mayores (pesos) a Money."""
        scale = Decimal(10) ** MINOR_UNITS[currency]
        minor = (Decimal(value) * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return cls(int(minor), currency)

    # -- aritmética -------------------------------------------------------

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"Monedas distintas: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    @classmethod
    def sum(cls, items: Iterable[Money], currency: str = "COP") -> Money:
        total = cls.zero(currency)
        for item in items:
            total = total + item
        return total

    # -- consultas --------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def units(self) -> Decimal:
        """Valor en unidades mayores. Solo para formateo, nunca para operar."""
        return Decimal(self.amount) / (Decimal(10) ** MINOR_UNITS[self.currency])

    def ratio_to(self, other: Money) -> Decimal:
        """|self| / |other|. Usado para expresar una diferencia como % del bruto."""
        self._check(other)
        if other.amount == 0:
            raise ZeroDivisionError("ratio_to sobre monto cero")
        return Decimal(abs(self.amount)) / Decimal(abs(other.amount))

    def format(self) -> str:
        sign = "-" if self.amount < 0 else ""
        whole = f"{abs(self.units):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{sign}${whole} {self.currency}"

    def __str__(self) -> str:
        return self.format()


def _normalize_separators(text: str) -> str:
    """Decide cuál de `.` y `,` es el separador decimal y devuelve algo que
    `Decimal` entienda."""
    if "," in text and "." in text:
        # El último que aparece es el decimal.
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
    elif "," in text:
        tail = text.rsplit(",", 1)[1]
        # "1,234" con 3 dígitos atrás es separador de miles, no decimal.
        decimal_sep = "," if len(tail) != 3 else ""
    elif "." in text:
        tail = text.rsplit(".", 1)[1]
        decimal_sep = "." if len(tail) != 3 else ""
    else:
        return text

    if decimal_sep == "":
        return text.replace(",", "").replace(".", "")

    thousands_sep = "." if decimal_sep == "," else ","
    return text.replace(thousands_sep, "").replace(decimal_sep, ".")
