# Conciliación contra el ERP — bancolombia

**Libro contable:** cuenta `111001` en Odoo

## 🔴 El ERP registra solo el 3% de lo que puede registrar

De **426** movimientos que ocurrieron y tienen cuenta donde asentarse, el libro contable registra **11** por $50.832.937,61 COP. Faltan **415** por registrar. Y hay **4** asiento(s) que el libro registra sin que ningún movimiento observado los respalde.

## Movimientos que el ERP no registra

Agrupados por tipo: importa más *qué clase* de hecho no se está contabilizando que la lista de casos.

| Tipo | Cantidad | Monto | Qué hacer |
|---|---:|---:|---|
| Créditos bancarios | 204 | $637.423.248,01 COP | registrar el asiento |
| Débitos bancarios | 211 | -$540.353.153,17 COP | registrar el asiento |

Neto pendiente de registrar: **$97.070.094,84 COP**.

## El ERP registra algo que no ocurrió

Un ERP que registra algo que no pasó es tan problema como uno al que le falta un registro.

| Fecha | Asiento | Monto |
|---|---|---:|
| 2025-12-29 | MISC/2025/12/0001 | -$20.000,00 COP |
| 2025-12-29 | MISC/2025/12/0002 | $100.000,00 COP |
| 2026-04-20 | BILL 212 | $10.000,00 COP |
| 2026-06-11 | BNK8/2026/00012 | $36.890,00 COP |

## Asientos sin confirmar

Existen en Odoo pero en borrador o anulados, así que no forman parte del libro formal. Hay que confirmarlos o descartarlos.

| Fecha | Asiento | Monto |
|---|---|---:|
| 2025-11-19 | Borrador de asiento | $0,00 COP |

## Coincidencias (11)

Cada fila lleva el identificador del movimiento y el del asiento: representan la misma cosa y se pueden abrir en ambos sistemas.

| Fecha | Monto | Movimiento | Asiento | Línea Odoo | Emparejado por |
|---|---:|---|---|---:|---|
| 2026-01-02 | $19.715.313,89 COP | `mov_2f80be0547d84711` | BNK8/2026/00001 | 3306 | monto y fecha |
| 2026-01-06 | $257.940,85 COP | `mov_2932243eb3fe0b76` | BNK8/2026/00002 | 3308 | monto y fecha |
| 2026-01-19 | $2.705.039,60 COP | `mov_cce86e929ab83e8d` | BNK8/2026/00003 | 3310 | monto y fecha |
| 2026-01-20 | $540.836,41 COP | `mov_b4dcaa4c31df8997` | BNK8/2026/00004 | 3312 | monto y fecha |
| 2026-01-21 | $10.054.512,99 COP | `mov_c859744ae373eb9a` | BNK8/2026/00005 | 3314 | monto y fecha |
| 2026-01-29 | $116.137,77 COP | `mov_ab47b3be4f3f4a45` | BNK8/2026/00006 | 3316 | monto y fecha |
| 2026-02-02 | $257.359,77 COP | `mov_bc86739ef2a013a1` | BNK8/2026/00007 | 3318 | monto y fecha |
| 2026-02-03 | $9.898.251,19 COP | `mov_37e6e3a3116af3f2` | BNK8/2026/00008 | 3320 | monto y fecha |
| 2026-03-04 | $5.627.962,31 COP | `mov_7687b3212ce7740b` | BNK8/2026/00009 | 3322 | monto y fecha |
| 2026-03-19 | $332.213,30 COP | `mov_1591670223406d32` | BNK8/2026/00010 | 3324 | monto y fecha |
| 2026-04-01 | $1.327.369,53 COP | `mov_1353c3b35d5aacdc` | BNK8/2026/00011 | 3326 | monto y fecha |

---

## Cómo leer esto

**Qué es el libro.** Todas las líneas contables de la cuenta `111001`, vengan del diario que vengan. No alcanza con mirar un diario: las líneas de esta cuenta aparecen en varios, y tomar uno solo dejaría afuera parte de la historia.

**Cómo se emparejan.** Por la referencia del asiento cuando esa referencia identifica una sola línea; si el mismo texto se repite en varias, por monto y fecha con unos días de margen —el asiento puede llevar la fecha del hecho o la de registración—.

**Qué no se compara.** Los asientos en borrador o anulados no forman parte del libro formal, y las ventas rechazadas no deberían estar en él.
