# ADR-0012 — El tarifario audita la ingesta, y el aviso no se persiste

**Estado:** aceptado
**Fase:** 1 (cierra un hueco abierto por ADR-0005)

## Contexto

ADR-0005 decidió que el tarifario es una **regla de validación**, no la fuente:

> El tarifario sirve para detectar anomalías: una fila que se desvía es otro
> medio de pago, otra tarifa negociada, o un error de parseo. El sistema lo
> reporta en vez de corregirlo silenciosamente.

Eso quedó escrito y **nunca se implementó**. Hasta este cambio,
`fee_schedule_for` se llamaba en exactamente dos lugares, los dos en
`reconcile/flow/engine.py`, y los dos solo en la rama donde el canal **no**
declaró el desglose. Nadie contrastaba el CSV contra el tarifario.

El agujero se ve al preguntar qué pasa si Wompi cambia la comisión, o si sube el
retefuente por regulación:

- **Lo declarado se ajusta solo.** El adapter del CSV lee las columnas y las
  ingiere tal cual. La tasa nueva entra sin tocar una línea de código, y la fila
  sigue cumpliendo `bruto − descuentos = neto`. Correcto: la fuente manda.
- **Lo inferido queda viejo.** Los descuentos se leen en 4 de 56 días; el resto
  se calcula con `WOMPI_FEES`. Esa mayoría empieza a dar residuos.
- **Y nadie se entera.** No hay síntoma que apunte a la causa. La conciliación
  declarada sigue impecable, la inferida se degrada, y el `unexplained` aparece
  sin explicación — que es exactamente lo que este sistema no puede permitirse.

El caso feo no es el ruidoso: es el que concilia perfecto mientras miente.

## Decisión

Un protocolo **opcional** en la capa de ingesta:

```python
class Auditor(Protocol):
    def audit(self, record: RawRecord) -> Iterator[str]: ...
```

`WompiDisbursementCsvAdapter` lo implementa: por cada fila contrasta `comisión`,
`iva comisión` y `retefuente` contra el tarifario vigente para su medio de pago y
su fecha, con `TARIFF_DEVIATION_TOLERANCE = $0,01`. `ingest()` lo llama después
de un parseo exitoso y acumula en `IngestionReport.warnings`.

Cuatro cosas que no son obvias:

**Va aparte de `parse`.** Traducir no es juzgar. Un adapter que evalúa mientras
lee termina corrigiendo en silencio, que es justo lo que ADR-0005 quiere evitar.

**El aviso no se persiste.** La alternativa natural era anotarlo en
`Movement.metadata`. No sirve: el movimiento es **inmutable** y no hay `UPDATE`,
así que un desvío detectado en enero seguiría afirmándose en marzo aunque la
config ya se hubiera corregido — documentación que miente, en la base. Auditar en
cada corrida siempre habla del ahora.

**Un desvío puede no tener movimiento donde colgarse.** El adapter no emite
movimientos en cero. Si una retención baja a 0 % por regulación, la fila deja de
producir movimiento — y esa desaparición es precisamente lo que hay que avisar.
Recorrer las filas, no los movimientos, es lo que lo hace visible.

**Un aviso no tumba `report.ok`.** El dato llegó completo y correcto; lo que
quedó viejo es nuestra hipótesis. Si un cambio de tarifa se viera igual que un
PDF corrupto, se atacarían al revés: uno se arregla editando `config.py`, el otro
arreglando el parser.

Los avisos se agrupan **por columna**, no por fila. Que **todas** las filas se
desvíen igual es la firma de un cambio de tarifa; una sola es una tarifa
negociada o un error de carga. Un aviso por fila haría que las dos se lean
idénticas, y se resuelven distinto. El mensaje lo dice explícitamente y nombra
`WOMPI_FEES` cuando corresponde, para que quien lo lea sepa qué editar.

Un medio de pago **sin tarifario** también avisa: no es un error —el CSV se
ingiere igual— pero significa que esas filas no se pueden verificar y que los
desembolsos de ese medio sin CSV no se van a poder inferir.

## Consecuencias

- Sobre los datos del challenge la auditoría da **cero avisos** en los 4 CSV: el
  9/9 exacto de ADR-0005 pasó de ser una afirmación a ser un test.
- Cambiar una tarifa sigue costando una entrada en `WOMPI_FEES` con
  `effective_from` / `effective_to`. Lo nuevo es que el sistema **pide** que se
  haga en vez de degradarse callado.
- Ingerir es un poco más caro: el CSV se recorre dos veces. Son 4 archivos de 9
  filas; el costo es irrelevante y compra que `parse` siga siendo un traductor
  puro.
- `strict=True` (tests y CI) convierte un **auditor roto** en error. Un desvío de
  tarifa no: los fixtures están congelados, y hacer fallar CI por una tasa nueva
  confundiría "el mundo cambió" con "el código está mal".

## Alternativas descartadas

**Validar dentro de `parse` y rechazar la fila.** Invierte ADR-0005: convierte el
tarifario en fuente de verdad y hace que el sistema no pueda descubrir nunca que
se equivocó. Una tarifa negociada legítima se volvería un error de ingesta.

**Anotar el desvío en `Movement.metadata`.** Descartada arriba: congela un juicio
en un objeto inmutable.

**Derivar el tarifario de los datos en vez de configurarlo.** Tentador —cada CSV
nuevo re-despeja las tasas— pero elimina la posibilidad misma de detectar una
anomalía: si la fórmula se ajusta a lo que llegó, nada se desvía nunca. Es el
mismo razonamiento por el que `ERP_UNREPRESENTABLE_KINDS` sale de config y no de
contar ceros (ADR-0011).
