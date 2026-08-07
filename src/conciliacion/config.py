"""Reglas de negocio. Versionadas en git a propósito.

Nada de acá es secreto ni depende del entorno. Son decisiones auditables:
cuentas, tarifas, ventanas de conciliación. Que vivan en git significa que un
cambio de tasa queda en el historial con su justificación, y que dos corridas
del mismo commit sobre la misma data dan el mismo resultado.

Si esto estuviera en el .env, "¿por qué en marzo la comisión era otra?" sería
irrespondible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

from .domain.ledger import Account
from .domain.money import Money
from .domain.movement import MovementKind

# ─── Cuentas ────────────────────────────────────────────────────────────────

WOMPI = Account(
    id="wompi",
    name="Cuenta Wompi (Anayap SAS)",
    currency="COP",
    role="channel",
)

BANCOLOMBIA = Account(
    id="bancolombia",
    name="Bancolombia — cuenta corriente",
    currency="COP",
    role="bank",
)

ACCOUNTS = (WOMPI, BANCOLOMBIA)

#: Fuera del alcance del challenge. Existe para la demostración de
#: extensibilidad (ADR-0009) y no está en `ACCOUNTS`: su adapter es real y
#: está testeado, pero su data es sintética y no se registra en `sources.py`.
POS = Account(
    id="pos",
    name="POS bancario (demo de extensibilidad)",
    currency="COP",
    role="channel",
)

# ─── Zona horaria ───────────────────────────────────────────────────────────

#: Toda fecha contable (`Movement.occurred_on`) se deriva en esta zona.
#:
#: No es un detalle: verificado contra el epoch embebido en los IDs de Wompi,
#: la columna `fecha` del CSV viene en COT (UTC−5). Hay transacciones a las
#: 21:22 COT, que en UTC caen al día siguiente. Agrupar por fecha UTC manda
#: esas ventas al batch equivocado y el día entero deja de conciliar.
TIMEZONE = "America/Bogota"

#: Calendario de días hábiles. Wompi liquida solo en hábiles: hay reportes de
#: transacciones sábado y domingo, pero ningún desembolso de fin de semana.
BUSINESS_CALENDAR = "CO"


# ─── Tarifas de Wompi ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Tarifario vigente en un período.

    ⚠️ Esto es una regla de VALIDACIÓN, no la fuente de verdad.

    Los montos de comisión, IVA y retención vienen DECLARADOS en el CSV de
    desembolsos de Wompi, y esos son los que se ingieren. El tarifario sirve
    para detectar anomalías: una fila que se desvía es otro medio de pago, otra
    tarifa negociada, o un error de parseo. El sistema lo reporta en vez de
    corregirlo silenciosamente.

    Invertir esta relación —calcular las comisiones en vez de leerlas— haría
    que el sistema no pueda descubrir nunca que se equivocó.
    """

    #: Desde cuándo aplica. Las tarifas cambian; los datos históricos no.
    effective_from: date
    effective_to: date | None
    payment_method: str

    #: comisión = trunc₂(rate × monto + fixed)
    commission_rate: Decimal
    commission_fixed: Money
    #: iva = trunc₂(iva_rate × comisión_SIN_truncar)
    iva_rate: Decimal
    #: retefuente = trunc₂(retefuente_rate × monto)
    retefuente_rate: Decimal

    def commission_exact(self, gross: Money) -> Decimal:
        """Comisión sin truncar, en unidades menores.

        Se expone porque el IVA se calcula sobre este valor, no sobre la
        comisión ya truncada. Verificado: usar la truncada falla en 2 de 9
        filas por un centavo.
        """
        return Decimal(gross.amount) * self.commission_rate + Decimal(self.commission_fixed.amount)

    def commission(self, gross: Money) -> Money:
        return Money(_trunc(self.commission_exact(gross)), gross.currency)

    def iva(self, gross: Money) -> Money:
        return Money(_trunc(self.commission_exact(gross) * self.iva_rate), gross.currency)

    def retefuente(self, gross: Money) -> Money:
        return Money(_trunc(Decimal(gross.amount) * self.retefuente_rate), gross.currency)

    def expected_net(self, gross: Money) -> Money:
        return gross - self.commission(gross) - self.iva(gross) - self.retefuente(gross)

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day <= self.effective_to)


def _trunc(value: Decimal) -> int:
    """Wompi trunca, no redondea. `7068.4775 → 7068.47`.

    Redondear desvía en 8 de 9 filas de la muestra."""
    return int(value.to_integral_value(rounding=ROUND_DOWN))


#: Derivado empíricamente de 9 transacciones en 4 reportes de desembolso
#: (14-04, 15-04, 27-04 y 04-05 de 2026). Las tres fórmulas dan exacto al
#: centavo en las 9 filas. Ver docs/adr/0005-tarifario-wompi.md para la
#: derivación completa.
#:
#: Todas las filas de la muestra son `medio de pago = CARD`. Otros medios
#: (NEQUI, PSE, transferencia) muy probablemente tengan otra tarifa: por eso
#: el tarifario está indexado por `payment_method` y una fila con un medio
#: desconocido se marca como no validable en vez de asumirse.
WOMPI_FEES = (
    FeeSchedule(
        effective_from=date(2026, 1, 1),
        effective_to=None,
        payment_method="CARD",
        commission_rate=Decimal("0.0235"),
        commission_fixed=Money(40_000),  # $400,00 COP
        iva_rate=Decimal("0.19"),
        retefuente_rate=Decimal("0.015"),
    ),
)


def fee_schedule_for(payment_method: str, day: date) -> FeeSchedule | None:
    """Tarifario aplicable, o `None` si no conocemos uno.

    `None` no es un error: significa que no podemos validar esa fila. El
    movimiento se ingiere igual con sus montos declarados y se marca como
    no verificado.
    """
    return next(
        (f for f in WOMPI_FEES if f.payment_method == payment_method and f.covers(day)),
        None,
    )


# ─── Ventanas de conciliación ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SettlementPolicy:
    """Cómo liquida un canal, y qué ventana usar para buscarlo en el banco.

    Está indexado por canal a propósito: es lo que hace que sumar un canal con
    otra cadencia sea **una entrada de configuración y no una regla nueva**. El
    motor de conciliación opera sobre `role="channel"` genérico y lee esta
    política; no sabe que existe Wompi.

    La ventana NO es una restricción de igualdad: es generación de candidatos, y
    el monto decide cuál matchea. Va más allá de T+1 por evidencia concreta —una
    transacción del miércoles 29-04 21:22 apareció en el desembolso del lunes
    04-05, dos días hábiles después de lo esperado (hipótesis: corte horario
    nocturno más el viernes 01-05 festivo). Con una ventana rígida de T+1 esa
    venta no conciliaba nunca.
    """

    #: Días hábiles entre la venta y la liquidación declarada por el canal.
    settlement_lag_business_days: int
    #: Ventana, en días hábiles, para buscar el crédito en el banco a partir
    #: de la fecha de liquidación.
    min_business_days: int = 0
    max_business_days: int = 3
    #: El canal consolida las ventas del período en un único giro.
    consolidates: bool = True


SETTLEMENT_POLICIES: dict[str, SettlementPolicy] = {
    # T+1 hábil, verificado: los 10 desembolsos de abril coinciden con 10
    # créditos bancarios en monto y fecha exacta.
    "wompi": SettlementPolicy(settlement_lag_business_days=1),
    # POS bancario: T+2. Fuera del alcance del challenge; entrada de ejemplo
    # que demuestra que una cadencia distinta no requiere código nuevo.
    # Ver ADR-0009.
    "pos": SettlementPolicy(settlement_lag_business_days=2),
}


def settlement_policy_for(ledger_id: str) -> SettlementPolicy:
    """Política del canal. Sin entrada, se asume T+1 con ventana amplia."""
    return SETTLEMENT_POLICIES.get(ledger_id, SettlementPolicy(settlement_lag_business_days=1))

#: Diferencia máxima tolerada al comparar un desembolso contra un crédito
#: bancario. Cero a propósito: los 10 casos verificados coinciden al centavo,
#: así que cualquier diferencia es información, no ruido. Si el banco resultara
#: redondear, se sube con justificación.
FLOW_AMOUNT_TOLERANCE = Money(0)

#: Residuo aceptable **por transacción** cuando los descuentos se infieren en
#: vez de leerse.
#:
#: No es un número elegido a ojo: es el error medido de la fórmula. Contrastada
#: contra los 55 desembolsos con cobertura, predice el neto exacto en 47 y falla
#: en 8 por exactamente $0,01 (el canal trunca en una etapa distinta a la que
#: modelamos). Con descuentos declarados la tolerancia es cero.
#:
#: Que este número exista y valga 1 centavo —en vez de un margen holgado que
#: tape cualquier cosa— es lo que permite afirmar que un match inferido es
#: confiable.
INFERENCE_TOLERANCE_PER_TRANSACTION = Money(1)

#: Pistas para acotar qué créditos bancarios vale la pena revisar cuando ningún
#: giro los explica.
#:
#: ⚠️ Es una **heurística de alcance, nunca una llave de match**. La descripción
#: del extracto cambió a mitad del período (`PAGO DE PROV WOMPI` → `PAGO DE TERC
#: WOMPI` el 14/04/2026); usarla para matchear perdería 53 de 58 líneas.
#:
#: Sin acotar, el reporte de "créditos sin explicar" listaría los 368
#: movimientos bancarios ajenos al canal (nómina, DIAN, seguros, servicios) y
#: sería inservible para el CFO. El motor deja constancia de que la detección es
#: heurística en la explicación de cada finding.
BANK_CHANNEL_HINTS: dict[str, tuple[str, ...]] = {
    "wompi": ("WOMPI",),
    "pos": ("POS", "DATAFONO"),
}


def bank_hints_for(ledger_id: str) -> tuple[str, ...]:
    return BANK_CHANNEL_HINTS.get(ledger_id, ())


# ─── Libros contables en Odoo ───────────────────────────────────────────────

#: Cuenta del plan que representa cada ledger en el ERP.
#:
#: **Por cuenta, no por diario.** Las líneas de `1110001` aparecen en tres
#: diarios distintos (Wompi Tarjetas, Bancolombia y Miscellaneous); tomar el
#: diario como libro perdería 13 de 53 líneas, incluidos los 11 giros al banco.
#: Ver ADR-0011.
#:
#: `1110001` funciona como cuenta puente: la venta la debita, el giro al banco
#: la acredita. Por eso el giro aparece una sola vez en el ERP.
ODOO_LEDGER_ACCOUNTS = {
    "wompi": "1110001",       # Wompi Tarjetas  (asset_cash)
    "bancolombia": "111001",  # Bank            (asset_cash)
}

#: Tipos de movimiento que **el plan de cuentas no puede representar** sobre la
#: cuenta del ledger, por más completo que esté el ERP.
#:
#: Verificado contra el Odoo real: los diarios 48 y 49 solo tocan `1110001`,
#: `420500` y `111001`. **No hay cuenta de comisión, ni de IVA, ni de
#: retención.** El bruto entra a la cuenta puente, el neto sale, y la diferencia
#: —las comisiones— queda ahí como saldo permanente, sin llevarse nunca a gasto.
#:
#: Sin esta distinción, la cobertura del ERP mezcla dos cosas opuestas: *"esto
#: debería estar asentado y no lo está"* (se arregla asentándolo) con *"no
#: existe la cuenta donde asentarlo"* (se arregla rediseñando el plan). Un
#: número que baja por las dos razones no dice qué hacer.
#:
#: Va acá y no derivado de los datos a propósito: que un tipo dé 0 coincidencias
#: podría ser casualidad. Que **no exista la cuenta** es un hecho del plan
#: contable, y afirmarlo requiere haberlo mirado.
ERP_UNREPRESENTABLE_KINDS: dict[str, frozenset[str]] = {
    "wompi": frozenset({MovementKind.FEE.value, MovementKind.TAX.value}),
}


def erp_unrepresentable_kinds(ledger_id: str) -> frozenset[str]:
    """Qué tipos no tienen cuenta en el plan donde ser asentados."""
    return ERP_UNREPRESENTABLE_KINDS.get(ledger_id, frozenset())


#: Sufijo de los ledgers que espejan el ERP. `wompi` ↔ `wompi_erp`.
ERP_SUFFIX = "_erp"


def erp_ledger_id(ledger_id: str) -> str:
    return f"{ledger_id}{ERP_SUFFIX}"


def erp_accounts() -> tuple[Account, ...]:
    """Cuentas espejo del ERP, una por ledger con libro contable."""
    return tuple(
        Account(
            id=erp_ledger_id(base.id),
            name=f"{base.name} — libro en Odoo",
            currency=base.currency,
            role="erp",
        )
        for base in ACCOUNTS
        if base.id in ODOO_LEDGER_ACCOUNTS
    )


# ─── Mapeo al plan de cuentas de Odoo ───────────────────────────────────────

#: Del enunciado. Las columnas del CSV de desembolsos mapean 1:1, lo que hace
#: que la conciliación contra el ERP compare cosas comparables.
ODOO_ACCOUNTS = {
    "sales": "420500",        # Otras Ventas          ← columna `monto`
    "iva_commission": "240810",  # IVA Descontable    ← `iva comisión`
    "commission": "530505",   # Gastos Bancarios      ← `comisión`
    "withholding": "236500",  # Retención en la Fuente← `retefuente`/`reteica`/`reteiva`
    "bank": "111001",         # Banco                 ← `total desembolsado`
}
