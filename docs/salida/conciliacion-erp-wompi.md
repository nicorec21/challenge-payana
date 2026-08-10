# Conciliación contra el ERP — wompi

**Libro contable:** cuenta `1110001` en Odoo

## 🔴 El ERP registra solo el 29% de lo que puede registrar

De **171** movimientos que ocurrieron y tienen cuenta donde asentarse, el libro contable registra **49** por $42.616.322,01 COP. Faltan **122** por registrar. Aparte hay **27** movimiento(s) (comisiones, iva y retenciones) por -$127.131,96 COP que **no tienen cuenta en el plan donde asentarse**: el bruto entra a la cuenta puente, el neto sale, y la diferencia queda ahí sin llevarse nunca a gasto. Eso no se resuelve registrando asientos. Y hay **2** asiento(s) que el libro registra sin que ningún movimiento observado los respalde.

## Movimientos que el ERP no registra

Agrupados por tipo: importa más *qué clase* de hecho no se está contabilizando que la lista de casos.

| Tipo | Cantidad | Monto | Qué hacer |
|---|---:|---:|---|
| Comisiones | 9 | -$70.792,55 COP | **no hay cuenta donde asentarlo** |
| Ventas cobradas | 75 | $162.274.869,00 COP | registrar el asiento |
| Giros al banco | 47 | -$213.586.718,81 COP | registrar el asiento |
| IVA y retenciones | 18 | -$56.339,41 COP | **no hay cuenta donde asentarlo** |

Neto pendiente de registrar: **-$51.311.849,81 COP**.

Las filas marcadas *no hay cuenta donde asentarlo* no son un descuido del contador: los diarios solo tocan la cuenta puente, ventas y banco. Corregirlo es una decisión de plan de cuentas, no de registración — cuáles crear está más abajo.

## Qué cuentas habría que abrir

Para los **27** movimientos de arriba que hoy no tienen dónde asentarse. Los tres códigos que nombra el enunciado **existen** en este Odoo, pero con otro nombre y sin uso en los diarios de Wompi y Bancolombia — así que abrirlos es decidir qué representan, no solo crearlos:

| Tipo | Cuenta propuesta | Qué es hoy en Odoo |
|---|---|---|
| Comisiones | `530505` | Currency Exchange Loss — 1 línea en todo Odoo |
| IVA y retenciones | `240810`, `236500` | Discountable VAT — 286 líneas, ninguna en los diarios 48/49; Withheld at source — 1 línea en todo Odoo |

## El ERP registra algo que no ocurrió

Un ERP que registra algo que no pasó es tan problema como uno al que le falta un registro.

| Fecha | Asiento | Monto |
|---|---|---:|
| 2026-01-06 | BNK8/2026/00002 | -$257.940,85 COP |
| 2026-02-02 | BNK8/2026/00007 | -$257.359,77 COP |

## Asientos sin confirmar

Existen en Odoo pero en borrador o anulados, así que no forman parte del libro formal. Hay que confirmarlos o descartarlos.

| Fecha | Asiento | Monto |
|---|---|---:|
| 2026-05-26 | — | $0,00 COP |
| 2026-05-26 | — | $100,00 COP |

## Coincidencias (49)

Cada fila lleva el identificador del movimiento y el del asiento: representan la misma cosa y se pueden abrir en ambos sistemas.

| Fecha | Monto | Movimiento | Asiento | Línea Odoo | Emparejado por |
|---|---:|---|---|---:|---|
| 2026-01-02 | -$19.715.313,89 COP | `mov_22b9b62919283e0a` | BNK8/2026/00001 | 3307 | monto y fecha |
| 2026-01-03 | $195.700,00 COP | `mov_a02669425ff7d417` | WMP/2026/00033 | 3284 | referencia |
| 2026-01-04 | $4.842.430,00 COP | `mov_7aba294e8277693d` | WMP/2026/00034 | 3286 | referencia |
| 2026-01-05 | $242.050,00 COP | `mov_1cb85c6e076437e8` | WMP/2026/00029 | 3276 | referencia |
| 2026-01-05 | $3.605.000,00 COP | `mov_4de5772422ed09e3` | WMP/2026/00031 | 3280 | referencia |
| 2026-01-05 | $8.896.877,00 COP | `mov_a0626caf862ac611` | WMP/2026/00032 | 3282 | referencia |
| 2026-01-05 | $161.813,00 COP | `mov_e682bffc9e2f7922` | WMP/2026/00030 | 3278 | referencia |
| 2026-01-10 | $156.597,00 COP | `mov_df6dc9ba1b45a874` | WMP/2026/00028 | 3274 | referencia |
| 2026-01-12 | $229.175,00 COP | `mov_4b8ced5cb83d3fc1` | WMP/2026/00027 | 3272 | referencia |
| 2026-01-14 | $212.180,00 COP | `mov_009d69f7f95f9d63` | WMP/2026/00024 | 3266 | referencia |
| 2026-01-14 | $170.612,00 COP | `mov_058514ad9f04e39a` | WMP/2026/00023 | 3264 | referencia |
| 2026-01-15 | $209.847,00 COP | `mov_19097ba63d350809` | WMP/2026/00025 | 3268 | referencia |
| 2026-01-15 | $253.483,00 COP | `mov_5b56f7d009c6db8c` | WMP/2026/00026 | 3270 | referencia |
| 2026-01-19 | -$2.705.039,60 COP | `mov_3099f43be6d10dea` | BNK8/2026/00003 | 3311 | monto y fecha |
| 2026-01-20 | $746.956,00 COP | `mov_7d22a2d2b13521c2` | WMP/2026/00022 | 3262 | referencia |
| 2026-01-20 | $9.013.479,00 COP | `mov_da70c6721cbb8592` | WMP/2026/00021 | 3260 | referencia |
| 2026-01-20 | -$540.836,41 COP | `mov_e007bce15c77dcc2` | BNK8/2026/00004 | 3313 | monto y fecha |
| 2026-01-21 | $2.496.000,00 COP | `mov_bc1b7833d33156de` | WMP/2026/00019 | 3256 | referencia |
| 2026-01-21 | -$10.054.512,99 COP | `mov_c21fc2bc9ca6aa20` | BNK8/2026/00005 | 3315 | monto y fecha |
| 2026-01-21 | $7.097.187,00 COP | `mov_e8242b517a45ca1b` | WMP/2026/00020 | 3258 | referencia |
| 2026-01-22 | $332.690,00 COP | `mov_fb831bd7ebe6f3ff` | WMP/2026/00017 | 3252 | referencia |
| 2026-01-23 | $436.926,00 COP | `mov_8961132ca1dddbbf` | WMP/2026/00018 | 3254 | referencia |
| 2026-01-28 | $121.849,00 COP | `mov_d0021d728182b39b` | WMP/2026/00016 | 3250 | referencia |
| 2026-01-29 | -$116.137,77 COP | `mov_e1087cf18ab7ad4b` | BNK8/2026/00006 | 3317 | monto y fecha |
| 2026-01-30 | $18.245.592,00 COP | `mov_6110623bd77914ea` | WMP/2026/00015 | 3248 | referencia |
| 2026-01-31 | $242.050,00 COP | `mov_04b32fb2cf3b5668` | WMP/2026/00014 | 3246 | referencia |
| 2026-01-31 | $173.113,00 COP | `mov_9f996fef873ad68a` | WMP/2026/00013 | 3244 | referencia |
| 2026-02-03 | -$9.898.251,19 COP | `mov_9e6f8bbe973e8d23` | BNK8/2026/00008 | 3321 | monto y fecha |
| 2026-02-06 | $622.635,00 COP | `mov_12c34b7af0552137` | WMP/2026/00011 | 3240 | referencia |
| 2026-02-06 | $2.630.071,00 COP | `mov_8f888e95328b22f4` | WMP/2026/00012 | 3242 | referencia |
| 2026-02-10 | $180.971,00 COP | `mov_a707f6977da16598` | WMP/2026/00010 | 3238 | referencia |
| 2026-02-11 | $527.886,00 COP | `mov_cd7b7291cffe3bb3` | WMP/2026/00009 | 3236 | referencia |
| 2026-02-12 | $14.049.974,00 COP | `mov_3afec93910d48470` | WMP/2026/00008 | 3234 | referencia |
| 2026-02-17 | $1.435.200,00 COP | `mov_cdea116870e40937` | WMP/2026/00007 | 3232 | referencia |
| 2026-03-03 | $1.039.188,00 COP | `mov_7ca3784667beb18e` | WMP/2026/00035 | 3288 | referencia |
| 2026-03-04 | -$5.627.962,31 COP | `mov_6c9696d4b95103d7` | BNK8/2026/00009 | 3323 | monto y fecha |
| 2026-03-05 | $3.605.000,00 COP | `mov_2a70faac955cdde9` | WMP/2026/00040 | 3298 | referencia |
| 2026-03-05 | $387.383,00 COP | `mov_47e1e59f43e142c4` | WMP/2026/00039 | 3296 | referencia |
| 2026-03-11 | $436.926,00 COP | `mov_17cca7257ed88aa4` | WMP/2026/00038 | 3294 | referencia |
| 2026-03-18 | $347.625,00 COP | `mov_57398c4caf229847` | WMP/2026/00036 | 3290 | referencia |
| 2026-03-18 | $212.180,00 COP | `mov_a4631cd9de728205` | WMP/2026/00037 | 3292 | referencia |
| 2026-03-19 | -$332.213,30 COP | `mov_675c6f9d5ad21408` | BNK8/2026/00010 | 3325 | monto y fecha |
| 2026-04-01 | -$1.327.369,53 COP | `mov_726b0149743744bf` | BNK8/2026/00011 | 3327 | monto y fecha |
| 2026-04-08 | $4.842.430,00 COP | `mov_86903ea01afe8e46` | WMP/2026/00006 | 3230 | referencia |
| 2026-04-11 | $3.605.000,00 COP | `mov_eb36c5cb66f967b8` | WMP/2026/00005 | 3228 | referencia |
| 2026-04-14 | $339.282,00 COP | `mov_4c97bd4f7d8967f9` | WMP/2026/00002 | 3222 | referencia |
| 2026-04-14 | $117.729,00 COP | `mov_52b4f82d0a33db27` | WMP/2026/00003 | 3224 | referencia |
| 2026-04-14 | $229.175,00 COP | `mov_d216bbadacee18ef` | WMP/2026/00004 | 3226 | referencia |
| 2026-04-24 | $243.698,00 COP | `mov_88d2c0e8a3e02f77` | WMP/2026/00001 | 3220 | referencia |

---

## Cómo leer esto

**Qué es el libro.** Todas las líneas contables de la cuenta `1110001`, vengan del diario que vengan. No alcanza con mirar un diario: las líneas de esta cuenta aparecen en varios, y tomar uno solo dejaría afuera parte de la historia.

**Cómo se emparejan.** Por la referencia del asiento cuando esa referencia identifica una sola línea; si el mismo texto se repite en varias, por monto y fecha con unos días de margen —el asiento puede llevar la fecha del hecho o la de registración—.

**Qué no se compara.** Los asientos en borrador o anulados no forman parte del libro formal, y las ventas rechazadas no deberían estar en él.
