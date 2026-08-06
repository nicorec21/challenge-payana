# ADR-0005 — Tarifario de Wompi: derivado, usado como validación, nunca como fuente

**Estado:** aceptado
**Fases:** 1 y 2

## Contexto

El enunciado advierte:

> Las comisiones e impuestos **no necesariamente vienen explícitos**; puede que
> tengas que inducirlos a partir de la diferencia entre lo que se cobró (bruto)
> y lo que llegó al banco (neto).

Ese *"puede que"* se resolvió mirando las fuentes reales:

| Fuente | Trae el desglose |
|---|---|
| CSV "reporte diario desembolso" | **sí**: comisión, IVA comisión, reteica, reteiva, retefuente, impoconsumo |
| API `/transactions` | no — solo el bruto |
| API `/disbursements` | no — solo el neto del batch |

O sea: el desglose existe, pero **solo en el CSV**, y el CSV se baja a mano de a
un día. Tenemos 4 de 56 días del período.

## La derivación

Sobre las 9 transacciones de los 4 CSV se despejaron tres fórmulas. Las tres dan
**exacto al centavo en 9 de 9**:

```
comisión      = trunc₂( 0.0235 × monto + 400 )
iva_comisión  = trunc₂( 0.19 × comisión_SIN_truncar )
retefuente    = trunc₂( 0.015 × monto )
```

Tres detalles que no se descubren sin contrastar contra datos:

- **Hay un componente fijo de $400.** Por eso `comisión / monto` daba 2.476% y
  no un porcentaje redondo. Con una sola fila el sistema es indeterminado: hacen
  falta dos montos distintos para despejar tasa y fijo.
- **Trunca, no redondea.** `7068.4775 → 7068.47`. Redondear desvía en 8 de 9 filas.
- **El IVA se calcula sobre la comisión *sin* truncar.** Calcularlo sobre la
  truncada falla por un centavo en 2 de 9. Es la clase de detalle que se asume
  mal y produce una diferencia que después se atribuye a "redondeo del banco".

Todo esto está fijado en `tests/test_config_fees.py`, parametrizado sobre las
9 filas reales.

## El límite de la fórmula: 47/55

Contrastada contra los **55 desembolsos** con cobertura completa (115
transacciones, 12× la muestra de derivación), prediciendo el neto del batch:

```
47 exactos / 55
 8 fallan por exactamente $0,01 — siempre prediciendo de más
```

Wompi trunca en una etapa distinta a la que modelamos cuando agrega varias
transacciones en un batch. No perseguimos el centavo: el resultado es más útil
que la corrección.

## Decisión

**El tarifario es una regla de validación, no la fuente de verdad.**

Los montos ingeridos son siempre los **declarados** por la fuente. La fórmula
solo se usa para:

1. detectar anomalías: una fila que se desvía es otro medio de pago, otra tarifa
   negociada, o un error de parseo — el sistema lo **reporta**, no lo corrige;
2. **inferir** el desglose cuando no hay CSV para ese día.

Invertir la relación —calcular las comisiones en vez de leerlas— produciría un
sistema que concilia perfecto siempre y **nunca puede descubrir que se equivocó**.

## Decisión asociada: no se bajan los 52 CSV faltantes

Se evaluó bajar a mano los 52 días restantes para tener todo declarado.
**Se decidió no hacerlo**, y usar los 4 disponibles como conjunto de validación
de la inferencia aplicada a los otros 52.

Motivos:

1. Con todo declarado, la capacidad que el enunciado pide explícitamente
   —inducir los descuentos de la diferencia bruto/neto— **no se ejercita nunca**.
2. El error de la inferencia está **medido**: ±$0,01 en 8 de 55. Eso no es una
   limitación, es un insumo: le da contenido numérico a `Confidence` y a
   `unexplained` en vez de dejarlos como etiquetas decorativas.
3. Los 4 CSV pasan de "datos incompletos" a **conjunto de contraste**:
   `DECLARED` vs `INFERRED` sobre exactamente los mismos hechos.

Consecuencia medible y buscada:

| Origen del ajuste | Precisión |
|---|---|
| `DECLARED` (CSV) | 9/9 exacto |
| `INFERRED` (fórmula) | 47/55, error acotado a ±$0,01 |

La jerarquía `DECLARED` > `INFERRED` de [ADR-0004](0004-explanation-como-objeto-de-dominio.md)
deja de ser una preferencia de diseño y pasa a ser una diferencia medida.

## Vigencia y medios de pago

`FeeSchedule` lleva `effective_from` / `effective_to` y está indexado por
`payment_method`. No es especulativo: la API devuelve también
`BANCOLOMBIA_QR`. En el período hay una sola transacción QR y está rechazada,
así que el tarifario CARD cubre el 100% de lo liquidado — pero un QR aprobado
mañana casi seguro tiene otra tarifa.

`fee_schedule_for(medio, fecha)` devuelve `None` cuando no conocemos un
tarifario. `None` **no es un error**: significa "no puedo validar esta fila".
El movimiento se ingiere igual con sus montos declarados y queda marcado como
no verificado. Un sistema que rechaza lo que no puede validar pierde datos;
uno que valida en silencio miente.

## Por qué vive en `config.py` y no en `.env`

Es una decisión auditable, no configuración de entorno. En git, un cambio de
tasa queda en el historial con su justificación y dos corridas del mismo commit
dan el mismo resultado. En una variable de entorno, *"¿por qué en marzo la
comisión era otra?"* es irrespondible.

## Consecuencias

- **Contra:** no podemos afirmar "todo exacto"; hay conciliaciones con residuo
  conocido de un centavo.
- **A favor:** podemos afirmar algo más fuerte —"esto es exacto, esto tiene ±$0,01,
  y el sistema sabe cuál es cuál"—, que es precisamente lo que el challenge evalúa.
