# ADR-0009 — Costo de sumar un canal: el POS, medido

**Estado:** aceptado
**Fase:** 1 (punto 3: extensibilidad)

## Contexto

El enunciado no pide afirmar que el diseño es extensible. Pide medirlo:

> **Extensibilidad.** El diseño tiene que hacer barato sumar nuevas fuentes. Por
> ejemplo: si mañana AA decide integrar también el POS bancario (otro formato,
> otra cadencia de liquidación), ¿cuánto código nuevo hace falta? **Mostralo.**

Son **dos** preguntas, y la segunda es la que discrimina:

1. ¿Cuánto cuesta un **formato** nuevo? La resuelve cualquier diseño con interfaces.
2. ¿Cuánto cuesta una **cadencia de liquidación** distinta? Revela si el motor
   estaba acoplado a Wompi.

## Decisión: un adapter real, no un ejemplo comentado

Se consideró dejar el adapter comentado con una nota en el README. Se descartó:
código comentado no compila, no se testea y **no se puede verificar**. Es una
afirmación disfrazada de código, y acá justamente lo que se pide es evidencia.

El adapter es real, está testeado y corre por el mismo pipeline. Lo sintético
son los datos, no el diseño.

## Qué es sintético y qué no

- **El layout NO es inventado.** `Asobancaria 2001` es un formato bancario
  colombiano de ancho fijo; el propio dashboard de Wompi lo ofrece como
  alternativa al CSV de desembolsos. Aporta rasgos que ninguna otra fuente del
  repositorio tiene: ancho fijo, **decimales implícitos** (montos sin punto), y
  registros de cabecera/detalle/control.
- **Los datos SÍ son sintéticos.** El POS está fuera del alcance del challenge y
  no hay archivos reales. Lo que se mide es el costo de extender, y eso es
  medible con un fixture inventado.

## No está registrado en `sources.py`

El adapter existe y se testea, pero el canal no se registra en el composition
root. Motivo: no meter datos sintéticos en el pipeline productivo. El registro
ocurre en el test, que es donde se demuestra que funciona.

`POS` tampoco está en `config.ACCOUNTS`, por lo mismo.

## El costo, medido

| Qué | Líneas |
|---|---|
| `ingest/adapters/pos_asobancaria.py` | 206 (43 de docstring del layout → **~128 de código**) |
| Fixture sintético | 5 |
| Registro en el composition root | **8** |
| Cadencia T+2 en `SETTLEMENT_POLICIES` | **1** |
| Tests | 209 |

Y lo que **no** se tocó:

```
src/conciliacion/domain/          ← 0 líneas
src/conciliacion/reconcile/       ← 0 líneas
src/conciliacion/storage/         ← 0 líneas
src/conciliacion/ingest/ports.py  ← 0 líneas
src/conciliacion/ingest/registry.py ← 0 líneas
src/conciliacion/ingest/connectors/ ← 0 líneas
```

## Los tres resultados que importan

**1. Cero líneas de connector nuevo.** El POS es un formato nuevo sobre un
transporte conocido (archivo local), así que reusa el `LocalFileConnector` que
ya usan los extractos PDF y los CSV de Wompi. Es el beneficio concreto de
separar Connector de Adapter ([ADR-0002](0002-connector-vs-adapter.md)): con una
abstracción fusionada, esta combinación costaría una clase entera.

**2. Cero líneas de dominio.** El POS no agregó ningún `MovementKind`,
`MovementStatus` ni campo de `Movement`. Es lo que valida haber mantenido
`MovementKind` chico y describiendo *qué le pasó a la plata* en vez de *de qué
fuente vino* ([ADR-0001](0001-modelo-canonico.md)). `test_el_dominio_no_cambio`
falla si un canal futuro necesita un kind propio, que sería la señal de que el
modelo empezó a filtrar la fuente hacia adentro.

**3. La cadencia distinta es una línea de configuración.** El POS liquida T+2,
Wompi T+1. Eso se expresa como un entero en `SETTLEMENT_POLICIES`:

```python
"wompi": SettlementPolicy(settlement_lag_business_days=1),
"pos":   SettlementPolicy(settlement_lag_business_days=2),
```

El motor opera sobre `role="channel"` genérico y lee la política; no sabe que
existe Wompi. Un canal sin política declarada cae en un default sensato en vez
de romper.

Esta es la respuesta a la segunda pregunta, que es la difícil: **una cadencia
nueva no es una regla nueva**.

## Lo que el adapter reusa sin escribir nada

- pipeline de ingesta, `sniff`, deduplicación, `IngestionReport`
- idempotencia por `(source_id, external_id)`
- persistencia en SQLite y roundtrip
- el criterio de autovalidación de [ADR-0007](0007-adapter-extracto-bancolombia.md):
  el archivo trae sus propios totales de control y si no cierran se rechaza entero
- el reparto disjunto de kinds de [ADR-0008](0008-reparto-disjunto-entre-fuentes.md):
  el ledger del POS cierra en cero por venta, igual que el de Wompi

## Consecuencias

- **Contra:** hay un adapter en el repositorio que no alimenta ninguna fuente
  productiva, y un fixture con datos inventados. Ambas cosas están declaradas
  acá y en el README para que nadie las confunda con datos del challenge.
- **A favor:** la afirmación "extender es barato" viene con un número, un
  `git diff` y una suite de tests que la respalda.
