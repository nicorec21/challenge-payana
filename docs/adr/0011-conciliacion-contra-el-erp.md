# ADR-0011 — Conciliación contra el ERP: el libro es otro ledger

**Estado:** aceptado
**Fase:** 3

## Contexto

Odoo lleva **partida doble**; el modelo del sistema es **partida simple**. Un
asiento cruza varias cuentas y hay que decidir cómo compararlo contra un ledger.

Antes de diseñar se exploró el ERP por XML-RPC (solo lectura). Lo que se
encontró resolvió la tensión en vez de forzar una elección a ciegas.

## El hallazgo: hay una cuenta puente

```
diario 48 "Wompi Tarjetas"  →  cuenta default 1110001 Wompi Tarjetas
diario 49 "Bancolombia"     →  cuenta default 111001 Bank
```

Asiento de venta (48):            Asiento de acreditación (49):

```
1110001  DEBE   243.698,00        111001   DEBE   1.327.369,53
420500   HABER  243.698,00        1110001  HABER  1.327.369,53
```

**`1110001 Wompi Tarjetas` ES el ledger `wompi` expresado como cuenta contable.**
La venta la debita; el giro al banco la acredita. Por eso el giro aparece **una
sola vez** en el ERP y no hay doble conteo entre libros — que era la duda
principal.

## Decisión 1 — El libro contable es un ledger más

Las líneas de Odoo se ingieren por el mismo pipeline (`OdooRpcConnector` +
`OdooLedgerAdapter`) y quedan en un ledger espejo: `wompi` ↔ `wompi_erp`.

La Fase 3 pasa a ser **comparar dos ledgers**. Cero conceptos nuevos, cero
cambios en el dominio.

## Decisión 2 — La proyección es una línea

```
monto_con_signo = debit − credit
```

Sin casos especiales por naturaleza de cuenta. Ambas cuentas relevantes son
`asset_cash`, así que debe = entrada y haber = salida: exactamente la convención
de signos del modelo. Verificado:

| Modelo | Odoo |
|---|---|
| `PAYMENT +243.698` (wompi) | 1110001 DEBE 243.698 |
| `SETTLEMENT −1.327.369,53` (wompi) | 1110001 HABER 1.327.369,53 |
| `BANK_CREDIT +1.327.369,53` (banco) | 111001 DEBE 1.327.369,53 |

Odoo devuelve `debit`/`credit` como **float**. Se convierten vía
`Decimal(str(...))` y nunca se opera en float: acá el error de redondeo sería
indistinguible de una diferencia contable real, que es justo lo que hay que
detectar.

## Decisión 3 — El libro se define por CUENTA, no por diario

Es la decisión que más cambia el resultado. Medido:

```
cuenta 1110001 → 53 líneas de TRES diarios
   Wompi Tarjetas 40 · Bancolombia 11 · Miscellaneous 2

cuenta 111001  → 16 líneas de TRES diarios
   Bancolombia 12 · Miscellaneous 3 · Vendor Bills 1
```

Tomar *"el diario 48"* como libro de Wompi perdería **13 de 53 líneas**,
incluidos los 11 giros al banco — o sea la mitad de la historia.

El enunciado habla de "un libro por cada ledger" y nombra los diarios 48 y 49;
la lectura correcta resultó ser la cuenta a la que esos diarios apuntan.

## Decisión 4 — La llave de match se decide contando, no a mano

`account.move.line.ref` sirve como llave **solo cuando es específica**:

```
libro de Wompi:        41 refs únicas sobre 51 líneas
libro de Bancolombia:   2 refs únicas sobre 12 líneas
```

Las del diario de ventas son la referencia de la transacción de Wompi
(`TKFGJOKOQFHWVIGU71QQQ` ↔ `tkfgjokoqfhwvigu71qqq`, comparadas sin distinguir
mayúsculas). Las acreditaciones comparten el texto `"Acreditación Wompi"`.

En vez de hardcodear cuál diario tiene refs buenas, el motor **cuenta**: una
referencia que aparece más de una vez no es llave, y esas líneas caen al match
por monto y fecha (±3 días, porque el asiento puede llevar la fecha del hecho o
la de registración).

Si el contador cambia el texto mañana, la regla sigue valiendo sin tocar código.

## Decisión 5 — Se recorre el LIBRO, no el ledger

Salió de un bug. El matcher producía diferencias de monto como esta:

```
ledger  −$3.166,63 (una COMISIÓN)  vs  erp  $117.729,00 (la venta)
```

Causa: en el CSV de desembolsos las comisiones **heredan la referencia de su
transacción**. Recorriendo el ledger, la comisión llegaba primero y se llevaba
la línea de la venta.

**Una referencia identifica una transacción, no un movimiento.** En el ledger
apunta a hasta cinco movimientos (pago, comisión, IVA, retención, giro); en el
libro, a una sola línea.

El matcher ahora recorre el libro y, entre los movimientos que comparten esa
referencia, elige el de monto correspondiente. Los 4 `AMOUNT_MISMATCH` falsos
desaparecieron y los matches subieron de 45 a 49. Hay una clase de tests
dedicada a que no vuelva.

## Decisión 6 — `draft` y `cancel` son un estado propio

El libro tiene asientos que no están confirmados:

```
libro Wompi:        51 posted · 2 cancel
libro Bancolombia:  15 posted · 1 draft
```

Un asiento en borrador o anulado **no forma parte del libro formal**, así que no
se compara. Pero tampoco se silencia: `NOT_POSTED` es un estado propio, porque
un borrador es trabajo a medio hacer que alguien tiene que confirmar o
descartar.

Se ingieren igual, con su estado en `metadata`. Descartarlos en ingesta
impediría reportarlos — mismo criterio que con las ventas rechazadas
(ADR-0008).

## Decisión 7 — Se reportan las dos direcciones

| Estado | Qué es |
|---|---|
| `MATCHED` | el movimiento y la línea representan lo mismo, con **los dos ids** |
| `AMOUNT_MISMATCH` | mismo hecho, otro número. El caso más grave |
| `MISSING_IN_ERP` | pasó y el ERP no lo registró |
| `MISSING_IN_LEDGER` | el ERP lo registra y nada lo respalda |
| `NOT_POSTED` | existe pero sin confirmar |

`MISSING_IN_LEDGER` importa tanto como su inverso: un ERP que registra algo que
no pasó es tan problema como uno al que le falta un registro.

Los faltantes se **agrupan por tipo de movimiento** en el reporte. *"El ERP no
registra ninguna comisión"* es una conclusión; 27 findings de comisión suelta
son ruido con la misma información.

## Decisión 8 — «No existe la cuenta» se acompaña de cuál crear

`ERP_UNREPRESENTABLE_KINDS` separa 27 movimientos por −$127.131,96 que no bajan
la cobertura porque **no hay dónde asentarlos**. Eso identifica el problema y
deja la acción a medias: quien lee el informe se entera de que hay que rediseñar
el plan de cuentas, no de cómo.

El enunciado dice *"las cuentas contables **a utilizar** son"* y nombra 530505,
240810 y 236500. Contra la instancia real la frase es falsa como descripción —los
tres códigos existen, ninguno se llama como dice el enunciado y ninguno aparece
en los diarios 48/49—, así que se lee como **instrucción de lo que hay que
proponer**. `ERP_PROPOSED_ACCOUNTS` es esa propuesta, y `coverage()` la emite en
`unrepresentable_proposed_accounts`.

El nombre dice `PROPOSED` porque afirmar que estas cuentas se usan sería mentir.
Junto va `ODOO_ACCOUNT_REALITY` con el nombre real de cada código, y un test que
exige que todo código propuesto esté ahí: proponer una cuenta sin haber mirado
qué es en la instancia repite el error que este mapa vino a corregir.

En el reporte del CFO va en **sección aparte** de los faltantes. «Falta el
asiento» lo resuelve quien contabiliza; «no existe la cuenta» lo resuelve quien
diseña el plan. En la misma tabla, el CFO le pide a la persona equivocada algo
que no puede hacer.

`TAX` mapea a **dos** cuentas: el modelo agrupa lo que el plan separa —IVA de la
comisión (descontable) y retención en la fuente (activo por cobrar)—. Repartir
entre las dos es del asiento, no de este mapa.

## Resultado sobre los datos del challenge

```
wompi vs cuenta 1110001        bancolombia vs cuenta 111001
  ledger 240 · libro 53          ledger 426 · libro 16
  cobertura del ERP: 24,7%       cobertura del ERP: 2,6%

  matched              49        matched              11
  missing_in_erp      149        missing_in_erp      415
  missing_in_ledger     2        missing_in_ledger     4
  not_posted            2        not_posted            1
```

Faltantes por tipo en el libro de Wompi:

```
fee            9      −$70.792,55     ninguna comisión registrada
tax           18      −$56.339,41     ni IVA ni retenciones
payment       75  $162.274.869,00
settlement    47 −$213.586.718,81
```

**Las cuentas del enunciado no se usan.** Los diarios solo tocan `1110001`,
`420500` y `111001`. Y los códigos no se llaman como dice el enunciado: `530505`
es *Currency Exchange Loss* (no "Gastos Bancarios") con 1 línea en todo Odoo, y
`236500` tiene 1. Consecuencia contable: el bruto entra a la cuenta puente, el
neto sale, y la diferencia —las comisiones— queda como saldo permanente sin
llevarse nunca a gasto.

## Validación cruzada con la Fase 2

Dos motores independientes, con reglas distintas, señalan los mismos registros:

| Fase 2 (flujo) | Fase 3 (ERP) |
|---|---|
| `$257.940,85` el 06/01: crédito sin giro que lo explique | `BNK8/2026/00002`: el ERP lo registra, nada lo respalda |
| `$257.359,77` el 02/02: ídem | `BNK8/2026/00007`: ídem |

## Consecuencias

- **Contra:** la comparación del ledger bancario contra su libro arroja 415
  faltantes, la mayoría de operaciones ajenas al canal (nómina, DIAN, seguros).
  Es cierto pero poco accionable; el reporte los agrupa en vez de listarlos.
- **A favor:** la proyección de partida doble a simple es una línea sin casos
  especiales, el libro entra por el pipeline existente, y cada discrepancia
  viene con los dos identificadores y su explicación.

## Alternativas descartadas

- **Tomar solo la línea de banco (`111001`) de cada asiento.** Compara netos y
  pierde el desglose; además no funciona para el libro de Wompi, cuya cuenta es
  la puente.
- **Comparar a nivel asiento agregado.** Un asiento cruza cuentas de dos
  ledgers distintos: agregarlo mezcla libros.
- **Filtrar `draft`/`cancel` en la ingesta.** Simplifica el motor y hace
  imposible reportar el caso.
