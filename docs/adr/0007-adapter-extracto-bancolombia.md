# ADR-0007 — Extracto de Bancolombia: coordenadas, clave sintética, autovalidación

**Estado:** aceptado
**Fase:** 1

## Contexto

Los extractos son 4 PDF (enero–abril 2026), ~11 KiB cada uno, de la cuenta de
ahorros 19300008472 de ALIMENTOS ALCAZAR SAS. Son PDF de texto, no escaneos: no
hace falta OCR. Entre los cuatro suman **426 líneas**, de las cuales **58**
mencionan a Wompi.

## Decisión 1 — Extracción por coordenadas, no por layout de texto

`pdftotext -layout` produce filas **desalineadas** en este PDF: las columnas
VALOR y SALDO salen asociadas a una fila distinta de la de FECHA y DESCRIPCIÓN.
El resultado parece correcto de lejos y asigna montos equivocados a las
descripciones, que es el peor modo de falla posible acá.

El adapter usa `pdfplumber`, agrupa palabras por su **centro vertical**
(tolerancia 2 pt, contra ~18 pt de separación entre filas) y ordena por `x0`
dentro de cada banda. Reconstruye las 426 líneas correctamente.

Es lo que justifica la dependencia de `pdfplumber` sobre una herramienta de
línea de comandos: no necesitamos texto, necesitamos posiciones.

## Decisión 2 — La unidad atómica es el archivo, no la línea

`RawRecord` es el PDF entero. Una línea suelta **no es interpretable**:

- no trae el año (está en el encabezado),
- no se puede validar sin la cadena de saldos completa.

La regla general que se desprende, y que se aplica al resto del sistema: el
`RawRecord` es la unidad más chica que **se interpreta sola**. Por eso en la API
de Wompi el `RawRecord` sí es el ítem individual — un objeto JSON de
`/transactions` se entiende solo.

Costo: el archivo entero en memoria. Con 11 KiB es irrelevante; con extractos de
cientos de MB habría que revisarlo.

## Decisión 3 — El año sale del `HASTA`, nunca del `DESDE`

Las filas traen `1/04`, sin año. El encabezado dice:

```
Enero:  DESDE: 2025/12/31   HASTA: 2026/01/31    ← filas "1/01".."31/01" = 2026
```

Derivar el año del `DESDE` corre el extracto de enero **un año entero** a 2025.

El `DESDE` es la fecha del **saldo anterior**, no de la primera fila. Corolario
útil: no hay solapamiento de movimientos entre extractos consecutivos, aunque
los períodos declarados parezcan pisarse.

Fijado en `test_enero_usa_hasta_no_desde`.

## Decisión 4 — `external_id` sintético, con el saldo adentro

El extracto **no trae referencia ni número de documento por línea**. La clave de
idempotencia hay que construirla.

Medido sobre las 426 líneas:

| Clave | Colisiones |
|---|---|
| `(periodo, fecha, descripción, valor)` | **18** |
| `(periodo, fecha, descripción, valor, saldo)` | **0** |

El caso que lo fuerza: el 1/04 hay **16 líneas idénticas** de
`SERVICIO E-MAILS ENVIADOS −280,00`. Son 16 cargos reales e indistinguibles
entre sí, salvo por el saldo acumulado, que codifica la posición en la secuencia.
Sin el saldo, colapsan a un movimiento y se pierden 15 cargos.

**Por qué el saldo y no un ordinal de línea:** el ordinal es estable solo si el
archivo no cambia de orden. Si el banco reemite el PDF con una línea más arriba,
todos los ordinales se corren y **se duplica el extracto entero** en la próxima
ingesta. El saldo no depende del orden de lectura.

## Decisión 5 — El extracto se autovalida; si no cierra, se rechaza entero

El PDF declara tres invariantes redundantes. Los tres se verifican en ingesta, y
los tres pasan en los 4 archivos:

| Invariante | Qué modo de falla detecta |
|---|---|
| `saldo_anterior + abonos − cargos == saldo_actual` | los totales se leyeron mal |
| cadena de saldos línea a línea sin roturas | se perdió o se duplicó una fila |
| `Σ movimientos == totales declarados` | falta un bloque entero |

Son redundantes a propósito: cada uno tapa un agujero de los otros. La cadena de
saldos no detecta un bloque faltante al final; el total sí.

**Un extracto que no cierra se rechaza completo, no parcialmente.** Conciliar
contra un extracto al que le falta una línea produce "faltantes" que no son
faltantes de plata sino de parseo — y desde el reporte del CFO son
indistinguibles. Es preferible no conciliar a conciliar mal en silencio.

## Decisión 6 — El adapter no clasifica

Una línea que dice `PAGO DE TERC WOMPI S.A.S.` se ingiere como `BANK_CREDIT`
común, no como liquidación de Wompi.

Decidir que *es* una liquidación es del motor de conciliación, que además debe
poder explicarlo con evidencia. Si el adapter la etiquetara, esa conclusión
entraría al sistema sin explicación asociada y sería indistinguible de un hecho
observado.

## Hallazgo que fija un requisito: la descripción cambia a mitad del período

```
PAGO DE PROV WOMPI S.A.S.   →  53 líneas  (ene, feb, mar, y abril hasta el 13/04)
PAGO DE TERC WOMPI S.A.S.   →   5 líneas  (abril, del 14/04 en adelante)
```

Mismo flujo de fondos, texto distinto, cambio el 14/04/2026.

Las tres liquidaciones que pudimos cruzar contra los CSV de Wompi son todas
`TERC`. Un matcher que derivara el patrón de lo verificable perdería **53 de 58**
líneas.

**La descripción es señal de scoring, nunca llave de match.**
`test_dos_descripciones_distintas_para_wompi` existe para que ese supuesto falle
ruidosamente si alguien lo introduce en el futuro.

## Supuesto explícito: los números de cuenta no coinciden

La API de Wompi declara que los desembolsos van a la cuenta `19300002179`. Los
extractos provistos son de la cuenta `19300008472`.

Los extractos son datos sintéticos construidos para el challenge (Alimentos
Alcázar es una empresa ficticia) sobre montos reales de Wompi. **El número de
cuenta no sirve como llave de join.**

El sistema asume que los desembolsos de Wompi acreditan en la cuenta de los
extractos, apoyado en que los 10 desembolsos de abril coinciden con 10 créditos
bancarios en monto **y fecha exacta**. Es un supuesto del challenge, no una
inferencia del sistema, y por eso se declara acá en vez de esconderse en el
código.
