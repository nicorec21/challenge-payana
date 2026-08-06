# ADR-0008 — Tres fuentes para un ledger: reparto disjunto de `MovementKind`

**Estado:** aceptado
**Fase:** 1

## Contexto

El ledger de Wompi se alimenta de tres fuentes que **se solapan parcialmente**:

| Fuente | Bruto por tx | Desglose fiscal | Neto girado | Estado | Link tx→batch |
|---|---|---|---|---|---|
| API `/transactions` | ✅ | ❌ | ❌ | ✅ | ✅ |
| CSV desembolso | ✅ | ✅ | ✅ | ❌ | ❌ |
| API `/disbursements` | ❌ | ❌ | ✅ | ✅ | ✅ (el id) |

El bruto aparece en dos fuentes; el neto en dos. Y la deduplicación es por
`(source_id, external_id)` ([ADR-0001](0001-modelo-canonico.md)), así que dos
fuentes describiendo el mismo hecho **no deduplican** — ni deberían, porque son
observaciones independientes y a veces querés compararlas.

Si cada adapter emitiera "todo lo que sabe", el bruto se contaría dos veces y el
saldo del ledger no significaría nada.

## Decisión

Cada adapter emite un conjunto **disjunto** de `MovementKind`: solo lo que esa
fuente es la única en saber.

```
api_transactions   →  PAYMENT       (bruto, estado, disbursement_id)
csv_desembolso     →  FEE + TAX     (el desglose fiscal)
api_disbursements  →  SETTLEMENT    (el neto girado)
```

Consecuencia buscada: para una transacción liquidada, el ledger cierra en cero.

```
+840.763,00   PAYMENT
 −20.157,93   FEE    (comisión)
  −3.830,00   TAX    (IVA sobre comisión)
 −12.611,44   TAX    (retención en la fuente)
−804.163,63   SETTLEMENT
─────────────
       0,00
```

Y un saldo distinto de cero **significa algo**: plata cobrada que todavía no se
giró. Es información contable real, no un artefacto del modelo.

Fijado en `test_transaccion_liquidada_cierra` y `test_no_hay_solapamiento_de_kinds`.

## El dato redundante se usa para validar, no para ingerir

El CSV **también** trae el bruto y el neto de cada transacción. No se ingieren
como movimiento: quedan en `metadata` como `declared_gross` / `declared_net`.

Así la redundancia entre fuentes se convierte en **chequeo cruzado** en vez de en
duplicado. Es el mismo criterio que con el tarifario en
[ADR-0005](0005-tarifario-wompi.md): cuando dos caminos independientes llegan al
mismo número, eso es información, y desperdiciarla sería una pena.

## Separar `FEE` de `TAX`

Podrían ser un solo kind "descuento". Se separan porque:

- van a **cuentas contables distintas** en Odoo (530505 Gastos Bancarios vs
  240810 IVA Descontable / 236500 Retención en la Fuente), y la Fase 3 compara
  contra esos asientos;
- responden preguntas distintas: la comisión es un costo negociable con el
  proveedor, la retención es un pago a cuenta de impuestos que la empresa
  recupera. Colapsarlas hace irrespondible *"¿cuánto nos costó Wompi este mes?"*.

## Se ingieren las ventas rechazadas

`api_transactions` emite `PAYMENT` para los 34 `DECLINED` y 8 `ERROR`, no solo
para los 115 aprobados.

Explicar por qué una venta **no** llegó al banco requiere tener esa venta en el
ledger. Una transacción descartada en ingesta es una pregunta que el sistema no
puede responder.

Consecuencia que costó un bug: `Ledger.balance()` sumaba todos los movimientos,
incluidos los rechazados. Con 34 ventas rechazadas el saldo quedaba inflado y el
cierre en cero era imposible. Ahora solo suman los aprobados. El movimiento
rechazado sigue en el ledger, pero no mueve plata.

Detalle que valida el modelo: de 157 transacciones, las 42 sin desembolso
asociado son **exactamente** las 34 `DECLINED` más las 8 `ERROR`. Cero aprobadas
sin liquidar. `disbursement = None` ⟺ la venta no se cobró.

## Verificación sobre datos reales

Ledger de Wompi, enero–abril 2026, 240 movimientos de las tres fuentes:

```
  bruto aprobado                  255.208.828,00
− descuentos declarados (9 tx)        127.131,96
− giros al banco                  263.904.355,80
─────────────────────────────────────────────────
= saldo                            −8.822.659,76
```

El saldo negativo se descompone **exacto** en dos causas conocidas:

| Componente | Monto |
|---|---|
| Desembolso `2800150` del 02/01/2026, que liquida ventas de diciembre 2025 (fuera de la ventana) | −19.715.313,89 |
| Comisiones aún no declaradas: 106 de 115 transacciones sin CSV | +10.892.654,13 |

Ningún peso queda sin justificar. El saldo negativo no era un error del modelo:
era la data diciendo la verdad sobre sus propios bordes.

Ese desembolso huérfano es además la línea `2/01 PAGO DE PROV WOMPI S.A.S.
19.715.313,89` del extracto de enero, y explica la diferencia de 2 líneas /
590.676,38 entre los 56 desembolsos de la API y las 58 acreditaciones de Wompi
en el banco.

## Corolario: el ledger necesita saber qué ventana cubre

El caso anterior obliga a distinguir dos situaciones que un matcher ingenuo ve
idénticas y que significan lo contrario:

- *no llegó la plata* → alerta;
- *no tengo datos de ese período* → nota al pie.

Un diciembre sin movimientos puede ser "no hubo ventas" o "nadie bajó ese mes".
Por eso `IngestionReport` registra la **ventana pedida**, no solo las fechas que
efectivamente trajo. La Fase 2 recorta la conciliación a la intersección de las
coberturas y reporta el resto como fuera de alcance, no como faltante.

## Alternativas descartadas

- **Una fuente autoritativa por ledger, las demás como respaldo.** Ninguna de las
  tres es completa: el CSV no tiene el estado ni las ventas rechazadas, la API no
  tiene el desglose fiscal. Elegir una implica perder datos que la otra sí tiene.
- **Deduplicar por identidad del hecho económico en vez de por fuente.**
  Requeriría que dos fuentes generaran el mismo `external_id` para el mismo
  hecho, lo que rompe la trazabilidad al origen y hace imposible comparar qué
  dice cada fuente. La comparación entre fuentes es justamente parte del valor.
