# ADR-0010 — Conciliación de flujo: dos saltos, no uno

**Estado:** aceptado
**Fase:** 2

## Contexto

El enunciado describe un salto: los pagos del canal se consolidan y aparecen en
el banco. Contrastado contra las fuentes reales resultaron ser **dos**, con
dificultad muy distinta:

```
ventas del día D
      │  T+1 hábil — el canal consolida y descuenta
      ▼
desembolso (batch)
      │  mismo día — verificado 10/10 en abril 2026
      ▼
crédito en el extracto bancario
```

El desfasaje temporal está entre la venta y el desembolso, **no** entre el
desembolso y el banco.

## Decisión 1 — El primer salto no se busca: el canal lo declara

Cada transacción de la API de Wompi trae su `disbursement_id`. Agrupar ventas en
su liquidación es un `GROUP BY` sobre un dato observado.

**Consecuencia fuerte: la ambigüedad que advierte el enunciado —"varios
subconjuntos de ventas que suman lo mismo"— no aplica por esta vía.**

Se evaluó implementar búsqueda de subconjuntos igual:

- **A. Solo el dato declarado.** Simple y correcto, pero no ejercita la
  capacidad que el enunciado parece querer ver.
- **B. Subset-sum como fallback** cuando el link no exista. En los 157 registros
  del período **siempre existe**, así que sería código sin caso de prueba real.
- **C. Ambos, comparando resultados.** El doble de trabajo para verificar un
  dato que el proveedor ya afirma.

**Se eligió A**, y el motor lo dice explícitamente en cada explicación (*"el
agrupamiento de ventas no se infirió: cada venta declara a qué desembolso
pertenece"*) en vez de fingir una búsqueda que no hizo.

El argumento: inventar ambigüedad donde el dato es inequívoco no es
sofisticación, es ruido. Si mañana aparece un canal sin ese campo, el fallback
se agrega con un caso de prueba real que hoy no existe.

## Decisión 2 — El segundo salto se busca por monto dentro de una ventana

La ventana es `[0, 3]` días hábiles desde el giro, aunque los 10 casos
verificados hayan caído **el mismo día**.

Motivo: hay evidencia de al menos un desfasaje mayor (la venta del 29-04 a las
21:22 apareció en el desembolso del 04-05). Con igualdad de fecha ese caso no
conciliaría nunca.

La ventana **genera candidatos**; el monto decide cuál gana. La tolerancia de
monto es **cero**: los 10 casos coinciden al centavo, así que cualquier
diferencia es información, no ruido.

## Decisión 3 — Qué NO se usa para matchear

**La descripción del banco.** Cambia a mitad del período (`PAGO DE PROV WOMPI` →
`PAGO DE TERC WOMPI` el 14/04/2026); usarla como llave perdería 53 de 58 líneas.

Se usa **solo** para acotar qué créditos huérfanos vale la pena reportar, y el
motor deja constancia en la explicación de que esa detección es heurística. Sin
acotar, el reporte listaría los 368 movimientos bancarios ajenos al canal
(nómina, DIAN, seguros, 63 cobros de e-mails a −280,00) y sería inservible.

**El número de cuenta.** La API declara `19300002179` y el extracto es de
`19300008472` (ADR-0007).

## Decisión 4 — `UNMATCHED_SETTLEMENT` ≠ `OUT_OF_COVERAGE`

La distinción más importante del reporte. En los datos se ven **idénticas** —un
giro sin crédito bancario— y significan lo contrario:

| Estado | Qué es | Acción |
|---|---|---|
| `UNMATCHED_SETTLEMENT` | falta plata | alerta |
| `OUT_OF_COVERAGE` | falta data | nota al pie |

Sin la distinción, el sistema reportaría como faltantes todos los giros de mayo
—cuyo extracto bancario simplemente no bajamos— y el CFO vería 47 problemas que
no existen. Un reporte con falsos positivos deja de leerse entero.

`FlowStatus.is_problem` es `False` para `OUT_OF_COVERAGE`, y el reporte del CFO
los pone en una sección aparte que empieza diciendo *"el sistema no afirma que
falte plata en estos casos: afirma que no tiene con qué compararlos"*.

## Decisión 5 — La cobertura es un parámetro, no una derivación

`reconcile_flow` acepta `coverage` explícito. Si no se pasa, lo deriva del rango
de movimientos observados — y esa derivación es **conservadora, no exacta**: un
extracto sin movimientos en marzo es indistinguible de un marzo que nadie bajó.

Quien tiene el dato bueno es la ingesta (`IngestionReport.requested_window`).

Salió de un bug: en los tests, un banco con un solo crédito el 30/04 "no cubría"
el 14/04, y todo caía en `OUT_OF_COVERAGE`. Correcto según la derivación,
inutilizable en la práctica.

## Decisión 6 — `Confidence` se degrada por señales, no se calcula

Cada señal débil baja un nivel:

| Nivel | Condición |
|---|---|
| `EXACT` | descuentos declarados, residuo cero, fecha dentro de lo esperado |
| `HIGH` | igual pero con descuentos inferidos y residuo dentro de tolerancia |
| `MEDIUM` | residuo mayor, o giro sin ventas que lo compongan |
| `LOW` | hay candidatos alternativos: requiere revisión humana |

La tolerancia para inferidos no es un margen elegido a ojo: es **un centavo por
venta**, el error medido de la fórmula (ADR-0005). Con descuentos declarados la
tolerancia es cero.

Deliberadamente conservador: es preferible que el CFO revise un match bueno a
que firme uno dudoso.

## Decisión 7 — El monto en disputa no es el total sin explicar

Se separan dos números que es tentador mezclar:

- **`disputed_amount`** — lo atribuible a los casos problemáticos.
- **`rounding_amount`** — los centavos que quedan en las conciliaciones que
  **sí** cerraron, por haber estimado comisiones en vez de leerlas.

Salió de un bug real: el veredicto decía `$894.105,98` mientras el detalle
sumaba `$894.105,90`. Los 8 centavos de diferencia eran el error de inferencia
repartido entre 52 matches exitosos, y no pertenecen a los problemas.

Un total que no coincide con la suma del detalle le enseña al lector a
desconfiar del reporte entero.

## Resultado sobre los datos del challenge

```
matched            55        exact     3
out_of_coverage     1        high     52
unmatched_bank      3        medium    4

conciliado   $263.600.926,28
en disputa       $894.105,90
redondeo               $0,08
```

Las cuentas cierran por ambos lados sin residuo:

- `55 matched + 3 huérfanos = 58` líneas Wompi del extracto
- `55 matched + 1 fuera de cobertura = 56` desembolsos de la API

Los 3 casos `exact` son exactamente los 3 desembolsos de abril con CSV
declarado; los 52 `high` usan la fórmula inferida. La jerarquía de ADR-0005
aparece sola en la salida.

## Consecuencias

- **Contra:** el motor no implementa búsqueda de subconjuntos, así que si
  apareciera un canal sin `disbursement_id` habría que agregarla. La detección
  de créditos huérfanos depende de una heurística por descripción que puede
  perder casos con texto inesperado — declarado en cada explicación.
- **A favor:** cada conclusión trae su explicación estructurada, con los
  movimientos que relaciona, el origen de cada ajuste, la ventana considerada y
  lo que descartó. El reporte distingue falta de plata de falta de datos, que es
  lo que lo hace utilizable.
