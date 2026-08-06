# ADR-0001 — Modelo canónico: Movement inmutable, Ledger por cuenta

**Estado:** aceptado
**Fase:** 1

## Decisión

Tres piezas ([`domain/`](../../src/conciliacion/domain/)):

- **`Money`** — entero en unidades menores (centavos) + moneda.
- **`Movement`** — hecho inmutable, con signo, perteneciente a exactamente un ledger.
- **`Ledger`** — flujo cronológico de movimientos de una sola cuenta.

Partida simple, como pide el enunciado.

## `Money` como entero, nunca float

La conciliación **compara montos por igualdad** y **acumula sumas** de cientos
de movimientos para inferir comisiones. Con float, el error de redondeo se
vuelve indistinguible de la comisión que justamente queremos deducir: no hay
forma de saber si una diferencia de $0.01 es un ajuste real o ruido de punto
flotante.

El constructor **rechaza floats con `TypeError`**. No es defensivo de más: es
la única forma de garantizar que ningún parser meta un `float()` por descuido,
y los parsers son la frontera con data externa sucia.

`Money.parse` tolera los formatos que aparecen de verdad: `1.234.567,89`
(CO/ES), `1,234,567.89` (US), `(1.234)` (negativo contable), `1.234-` (signo al
final, típico de mainframe bancario). Cada uno tiene un test.

## `Movement` inmutable con ID determinista

`Movement.id = sha256(source_id + external_id)[:16]`.

Dos propiedades que importan:

- **Determinista entre corridas.** Dos ejecuciones sobre la misma data producen
  los mismos IDs. Los reportes son diffeables y una explicación que cita
  `mov_a1b2...` sigue siendo válida mañana. Con UUID random, cada corrida
  produce un reporte distinto sobre datos idénticos.
- **Deriva solo de la clave de la fuente**, no del contenido. Si la fuente
  corrige el monto de `TX-1`, sigue siendo el mismo hecho corregido, no un hecho
  nuevo.

Inmutable: si una fuente corrige un dato, se ingiere un movimiento nuevo. No
hay `UPDATE` sobre movimientos.

## Idempotencia por `(source_id, external_id)`

Reingerir el mismo extracto no duplica. La dedup es por **clave de la fuente**,
no por contenido: dos comensales pueden pagar $100 el mismo día y son dos
movimientos distintos. Deduplicar por `(fecha, monto)` — tentador y común —
borraría ventas reales.

Garantizado en dos capas: en memoria por `Ledger`, y en disco por
`UNIQUE(source_id, external_id)` en SQLite (ADR-0003).

## Se ingiere todo, incluso lo que no concilia

`MovementStatus` permite `DECLINED`, `VOIDED`, `PENDING`. Solo `APPROVED`
participa de la conciliación de flujo, pero **el resto se ingiere igual**:
explicar por qué un pago *no* llegó al banco requiere tener ese pago en el
ledger. Un movimiento descartado en ingesta es una pregunta que el sistema no
puede responder.

Mismo criterio en `MovementKind.OTHER`: preferimos un movimiento sin clasificar
a un movimiento perdido.

## `MovementKind` deliberadamente chico

`kind` dice **qué le pasó a la plata**, no de qué fuente vino (`source_id`) ni
cómo se contabiliza (eso es del ERP). Consecuencia buscada: agregar un canal
nuevo no agrega kinds. Si sumar el POS requiriera un `POS_SETTLEMENT`, el
modelo estaría filtrando la fuente dentro del dominio.

## Orden estable

`Ledger.movements` ordena por `(occurred_on, id)`. El desempate por ID
determinista —y no por orden de inserción— hace que el reporte no dependa del
orden en que se leyeron los archivos. Hay un test que ingiere el mismo set al
derecho y al revés y exige la misma salida.

## Consecuencias

- **Contra:** `Money` obliga a pensar en centavos en todo el código; la
  inmutabilidad obliga a reingerir en vez de corregir.
- **A favor:** aritmética exacta, corridas reproducibles, ingesta idempotente
  por construcción.
