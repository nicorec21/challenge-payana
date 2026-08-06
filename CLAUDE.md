# CLAUDE.md — contexto y convenciones

Conciliación contable de Alimentos Alcázar: convertir fuentes heterogéneas
(Wompi, extractos de Bancolombia, Odoo) en una historia explicable del dinero.

**Este archivo contiene lo que NO se deduce leyendo el código**: hechos sobre
los datos reales que costaron investigación, invariantes que no se pueden
romper, y trampas que ya mordieron. Lo derivable del código está en el código;
las decisiones y sus porqués, en `docs/adr/`.

---

## ⚠️ Regla de oro: la documentación se actualiza en el mismo cambio

**Ningún PR se da por terminado sin actualizar la documentación que su cambio
volvió falsa.** No en un commit aparte, no "después": en el mismo cambio.

Un dato desactualizado acá es peor que no tenerlo. Alguien —persona o agente—
lo va a leer y actuar sobre él sin verificar, porque el resto del archivo es
confiable. Documentación que miente a veces se lee como documentación que miente
siempre, y entonces deja de servir.

### Dónde va cada cosa

| Descubriste… | Va a |
|---|---|
| Un hecho verificado sobre los datos reales | `CLAUDE.md` → **Hechos sobre los datos reales** |
| Algo que engañó o va a engañar a quien siga | `CLAUDE.md` → **Trampas conocidas** |
| Una propiedad que no se puede romper | `CLAUDE.md` → **Invariantes** + un test que la fije |
| Una decisión con trade-off y alternativas descartadas | **ADR nuevo** en `docs/adr/`, numerado |
| Un comando, una fuente, una forma de correr algo | `README.md` |
| Una convención de código nueva | `CLAUDE.md` → **Convenciones** |
| Que una decisión previa era incorrecta | **Editá el ADR** marcando el cambio. No lo borres: el razonamiento viejo explica por qué el código es como es |

### Los números son lo primero que se desactualiza

Este archivo y el README afirman cantidades concretas: 242 tests, 106/136,
426 movimientos, 58 líneas de Wompi, 9/9 declarado, 47/55 inferido,
−$8.822.659,76 de saldo. **Cada uno es verificable corriendo algo.**

Si tu cambio mueve alguno, actualizalo en todos los lugares donde aparece
(`CLAUDE.md`, `README.md`, ADRs). Un número que ya no cierra le enseña al
próximo lector a desconfiar de todos los demás.

### Qué NO documentar

- Lo que el código ya dice. Si hace falta explicar *qué* hace una función,
  arreglá la función, no agregues un párrafo.
- Estado transitorio ("estoy trabajando en X"). Eso es una tarea, no contexto.
- Lo que se puede sacar de `git log`.

### Cuando algo se descubre, se anota aunque no toque escribir código

Los mejores contenidos de este archivo salieron de investigar, no de programar:
el timezone COT, la descripción del banco que cambia el 14/04, las 18 colisiones
sin el saldo, el ±$0,01 de la inferencia. Ninguno estaba en un diff.

Si averiguaste algo sobre los datos o sobre una API externa y no lo escribís,
el próximo lo vuelve a averiguar. Y puede llegar a otra conclusión.

---

## Comandos

```bash
pip install -e ".[dev]"
pytest                                        # 242
pytest -m unit                                # 106 — solo dominio, milisegundos
pytest -m integration                         # 136 — pipeline sobre fixtures
ruff check .
conciliacion ingest bancolombia --offline     # sin credenciales
conciliacion ingest wompi                     # requiere .env
conciliacion sources --offline
conciliacion show wompi
```

`--offline` omite las fuentes de red y reprocesa desde `data/raw/`.

**Ningún test toca la red, y se hace cumplir.** `tests/conftest.py` bloquea la
creación de sockets; un test que intente salir falla con un mensaje explícito.
Para uno que de verdad necesite red: `@pytest.mark.network`, y queda excluido de
la corrida por defecto.

El marcado `unit`/`integration` es automático por módulo (`UNIT_MODULES` en
`conftest.py`), no hay que acordarse de ponerlo.

CI son **3 checks**: `test (3.11)`, `test (3.13)` —ruff + unit + integration +
cobertura como steps— y `smoke`, que instala sin dependencias de desarrollo ni
credenciales y verifica que la ingesta offline dé 426 movimientos y que
reingerir dé 0 nuevos.

Los steps no son jobs a propósito: la suite tarda ~9 s y un runner extra cuesta
más setup del que ahorra. Si agregás verificaciones, agregá **steps**; un job
nuevo solo se justifica si necesita un entorno distinto (como `smoke`, que
instala sin `[dev]`).

---

## El negocio en cinco líneas

- Alimentos Alcázar vende por Wompi (links de pago) y por POS bancario.
- Wompi cobra bruto, descuenta comisión + IVA + retenciones, y **al día hábil
  siguiente** gira el neto a la cuenta de Bancolombia.
- Bancolombia recibe esos giros mezclados con toda la operación de la empresa.
- Odoo es el system of record: un libro por cuenta.
- **Scope:** Wompi + Bancolombia. El POS es solo demo de extensibilidad.

Hay **dos conciliaciones distintas**, y no hay que mezclarlas:
1. **Flujo** (Fase 2): ¿la plata que Wompi prometió llegó al banco?
2. **ERP** (Fase 3): ¿el libro de Odoo refleja lo que pasó?

---

## Hechos sobre los datos reales

Verificados contra los datos del challenge. **No los re-deduzcas: verificalos si
dudás, pero no los asumas distinto.**

### Zona horaria — el más peligroso

La columna `fecha` del CSV de Wompi y los `created_at` de la API vienen en
**America/Bogota (UTC−5)**, y `Movement.occurred_on` se deriva siempre ahí.

Hay transacciones a las **21:22 COT**, que en UTC caen al día siguiente.
Agrupar por fecha UTC las manda al batch equivocado y el día entero deja de
conciliar. Verificado contra el epoch embebido en el ID de transacción
(`1203607-`**`1776117137`**`-25637`), que reconstruye la columna `fecha` exacto
en 9/9 filas.

### Fechas y cadencia

| Hecho | Evidencia |
|---|---|
| venta → desembolso = **T+1 hábil** | 10/10 en abril 2026 |
| desembolso → crédito bancario = **mismo día** | 10/10, monto y fecha exactos |
| Wompi consolida fin de semana | el archivo del lunes 27/04 trae vie 24 + sáb 25 + dom 26 |
| Hay ventas los 7 días, liquidaciones solo hábiles | reportes de transacciones sáb y dom, ningún desembolso |

**Una anomalía sin explicar**: la venta del miércoles 29-04 21:22 apareció en el
desembolso del lunes 04-05, dos días hábiles después. Hipótesis (un solo dato,
no confirmada): corte horario nocturno + viernes 01-05 festivo. Por eso la
ventana de matching es `[0, 3]` días hábiles y **no** igualdad de fecha.

### Fórmulas de Wompi (medio de pago `CARD`)

```
comisión      = trunc₂( 0.0235 × monto + 400 )
iva_comisión  = trunc₂( 0.19 × comisión_SIN_truncar )
retefuente    = trunc₂( 0.015 × monto )
```

- Hay un **componente fijo de $400**. Con una sola fila el sistema es indeterminado.
- **Trunca, no redondea.** Redondear desvía en 8 de 9 filas.
- El IVA se calcula sobre la comisión **sin truncar**. Sobre la truncada falla en 2 de 9.

**Precisión medida:**

| Origen | Resultado |
|---|---|
| `DECLARED` (CSV) | 9/9 exacto |
| `INFERRED` (fórmula) | 47/55 desembolsos; los 8 restantes fallan por **exactamente $0,01** |

Ese ±$0,01 es el insumo numérico de `Confidence` y `unexplained`. No lo borres.

### Volumen (ene–abr 2026)

```
Wompi API:  157 transacciones · 56 desembolsos
   115 APPROVED CARD (todas con desembolso)
    34 DECLINED · 8 ERROR (ninguna con desembolso)
     1 BANCOLOMBIA_QR, rechazada
Bancolombia: 426 líneas en 4 PDF · 58 mencionan Wompi
CSV desembolso: 4 de 56 días (decisión deliberada, ver ADR-0005)
```

`disbursement = None` ⟺ la venta no se cobró. **Cero aprobadas sin liquidar.**

### El saldo del ledger Wompi da −$8.822.659,76 y está bien

```
−19.715.313,89   desembolso 2800150 (02/01/2026) que liquida ventas de dic-2025,
                 fuera de la ventana. Es la línea "2/01 PAGO DE PROV WOMPI" del extracto.
+10.892.654,13   comisiones aún no declaradas (106 de 115 tx sin CSV)
```

Ningún peso sin justificar. Si tocás la ingesta y este número cambia, algo se rompió.

---

## Trampas conocidas

**La descripción del banco cambia a mitad del período.**
`PAGO DE PROV WOMPI S.A.S.` (53 líneas, ene–13/04) → `PAGO DE TERC WOMPI S.A.S.`
(5 líneas, desde 14/04). Mismo flujo, texto distinto.
**La descripción es señal de scoring, NUNCA llave de match.** Un matcher que la
use como llave pierde 53 de 58 líneas. `test_dos_descripciones_distintas_para_wompi`
existe para que eso falle ruidosamente.

**Montos que se repiten en fechas distintas.**
`3.449.635,18` está el 6/03 y el 13/04. `1.140.426,49` el 27/01 y el 8/04. Monto
+ descripción no identifican un crédito; la ventana temporal es correctitud, no
optimización.

**`218.852,51` es la trampa fina.** Es el `total desembolsado` de **una sola
transacción** dentro del batch del 15-04 (que suma 926.373,11), y **también** es
un crédito bancario completo el 24/03. Un matcher que busque subconjuntos sin
restricción temporal casa el crédito del 24/03 con una transacción del 14/04, con
monto exacto. Solo la fecha lo evita.

**Los números de cuenta no coinciden.** La API de Wompi declara destino
`19300002179`; los extractos son de `19300008472`. Los extractos son sintéticos
(Alimentos Alcázar es ficticia) sobre montos reales. **La cuenta no sirve como
llave de join.**

**El año de las líneas del extracto sale del `HASTA`, nunca del `DESDE`.**
Enero dice `DESDE: 2025/12/31 HASTA: 2026/01/31` y sus filas son de **2026**.

**`pdftotext -layout` desalinea las columnas** VALOR/SALDO respecto de
FECHA/DESCRIPCIÓN en estos PDF. Parece correcto de lejos y asigna montos a la
línea equivocada. Se usa `pdfplumber` agrupando palabras por centro vertical.

**El filtro `disbursement_id` de la API se ignora silenciosamente.** Con y sin
filtro devuelve lo mismo. Hay que traer el rango y agrupar del lado cliente.

**`/transactions` devuelve PII real** (mail, nombre, teléfono, BIN, últimos 4,
nombre del tarjetahabiente, device fingerprint). Nunca la persistas. Ver abajo.

---

## Invariantes que no se pueden romper

Cada uno tiene tests. Si alguno falla, no lo "arregles" relajándolo.

1. **`total_desembolsado = monto − comisión − iva − reteica − reteiva − retefuente − impoconsumo`**
   al centavo, por fila del CSV. 9/9.
2. **Extracto bancario**: `saldo_anterior + abonos − cargos == saldo_actual`; la
   cadena de saldos línea a línea sin roturas; `Σ movimientos == totales`.
   4/4 archivos, 426 líneas. Un extracto que no cierra se **rechaza entero**.
3. **El ledger de Wompi cierra en cero** por transacción liquidada:
   `+bruto −comisión −impuestos −neto = 0`.
4. **Idempotencia**: reingerir no duplica. Garantizada por
   `UNIQUE(source_id, external_id)` en el esquema, no por código.
5. **Cero PII en disco.** El test recorre lo emitido y lo archivado buscando
   cadenas sensibles, incluido un `@` suelto.

---

## Convenciones

**`Money` es un entero en centavos. Nunca float.** El constructor rechaza floats
con `TypeError` a propósito: los parsers son la frontera con datos sucios. Con
float, el error de redondeo se vuelve indistinguible de la comisión que
queremos inferir.

**`Movement` es inmutable.** Si la fuente corrige un dato, se ingiere un
movimiento nuevo. No hay `UPDATE`. Su `id` es
`sha256(source_id + external_id)[:16]` — determinista entre corridas, para que
los reportes sean diffeables y una explicación que cita `mov_a1b2…` siga siendo
válida mañana.

**Los adapters no hacen I/O.** Reciben `bytes` o un dict, devuelven movimientos.
Es lo que los hace testeables con un fixture en memoria.

**Los adapters no clasifican.** Una línea que dice WOMPI se ingiere como
`BANK_CREDIT` común. Decidir que *es* una liquidación es del motor, que además
debe explicarlo. Si el adapter la etiquetara, esa conclusión entraría sin evidencia.

**Se ingiere todo, incluso lo que no concilia.** `DECLINED`, `ERROR`, `OTHER`.
Explicar por qué una venta *no* llegó al banco requiere tenerla. Un movimiento
descartado en ingesta es una pregunta que el sistema no puede responder.
`Ledger.balance()` solo suma los aprobados.

**Las fuentes de un mismo ledger emiten `MovementKind` disjuntos.** Si dos
emitieran el pago, se contaría dos veces (distinto `source_id` ⇒ no deduplican).

```
wompi_api_transactions  → PAYMENT
wompi_disbursement_csv  → FEE + TAX
wompi_api_disbursements → SETTLEMENT
```

El dato redundante (el CSV también trae bruto y neto) va a `metadata` para
**validar**, no se ingiere.

**La explicación es un objeto, no un string.** Un solo `Explanation` estructurado
por conclusión, y dos renderers (Markdown para el CFO, JSON para la IA). Si cada
salida armara su texto, divergirían y el sistema afirmaría dos cosas distintas
sobre el mismo hecho.

**Reglas de negocio en `config.py` (git), secretos en `.env` (ignorado).** Una
tasa de comisión en una variable de entorno hace irrespondible *"¿por qué en marzo
era otra?"*.

**Allowlist, nunca denylist, para campos de fuentes externas.** Si el proveedor
agrega un campo con PII mañana, una denylist lo persiste en silencio.

---

## Mapa

```
src/conciliacion/
├── domain/          modelo canónico, Python puro, cero deps
│   ├── money.py         entero en centavos
│   ├── movement.py      inmutable, id determinista
│   ├── ledger.py        movimientos de UNA cuenta
│   └── explanation.py   por qué el sistema concluye lo que concluye
├── ingest/
│   ├── ports.py         Connector (cómo llegan los bytes) ⟂ Adapter (qué significan)
│   ├── registry.py      GenerarLedger / RegistrarMovimientos
│   ├── connectors/      local_file, wompi_api
│   └── adapters/        bancolombia_pdf, wompi_api, wompi_disbursement_csv,
│                        pos_asobancaria (demo, no registrado)
├── reconcile/
│   ├── calendar.py      días hábiles CO con Ley Emiliani
│   ├── flow/            Fase 2 — vacío
│   └── erp/             Fase 3 — vacío
├── storage/         SQLite, un archivo, sin ORM
├── config.py        cuentas, tarifario, políticas de liquidación
├── settings.py      secretos desde .env
├── sources.py       composition root — el archivo que mide la extensibilidad
└── cli.py           punto de entrada, no interfaz de usuario
```

`data/raw/` se versiona (evidencia, permite correr recién clonado).
`data/out/`, `.env` y `*.db` no.

---

## Estado

**Fase 1 completa.** Modelo, ingesta, persistencia, CLI, 9 ADRs, extensibilidad
medida. 242 tests.

**Fase 2 (flujo canal→banco): no empezada.** Tiene todo lo que necesita:
- `disbursement_id` declarado por Wompi en cada transacción ⇒ agrupar pagos en su
  liquidación es un `GROUP BY`, no una búsqueda de subconjuntos. **La ambigüedad
  que advierte el enunciado no aplica por esta vía.**
- 10/10 desembolsos verificados contra créditos bancarios.
- El error de la inferencia, medido.
- `SettlementPolicy` para generar candidatos.
- `IngestionReport.requested_window` para distinguir *"no llegó la plata"* de
  *"no tengo datos de ese período"* — que se ven idénticos y significan lo opuesto.

**Fase 3 (ERP/Odoo): no empezada.** Decisión abierta y grande: Odoo es partida
doble y el modelo es partida simple. Hay que decidir cómo colapsar un asiento en
un movimiento (¿la línea de banco 111001? ¿el neto?). Merece su propio ADR.

Cuentas de Odoo: Ventas 420500 · IVA comisiones 240810 · Comisiones 530505 ·
Retenciones 236500 · Banco 111001. Diarios: 48 = Wompi, 49 = Bancolombia.

---

## Decisiones (`docs/adr/`)

| | |
|---|---|
| 0001 | Movement inmutable, id determinista, `Money` entero |
| 0002 | Connector ⟂ Adapter: N+M clases, no N×M |
| 0003 | SQLite stdlib sin ORM; crudos en archivos |
| 0004 | La explicación es estructura, no texto |
| 0005 | El tarifario valida, no es fuente. `DECLARED` 9/9 vs `INFERRED` 47/55 |
| 0006 | Allowlist de PII en el borde: falla cerrada |
| 0007 | PDF por coordenadas, clave sintética con saldo, autovalidación |
| 0008 | Tres fuentes, kinds disjuntos, el ledger cierra en cero |
| 0009 | Costo de sumar el POS, medido |

---

## Al trabajar acá

- **Actualizá la documentación en el mismo cambio.** Ver la regla de oro arriba.
  Si tu PR movió un número, un invariante o un hecho de este archivo y no lo
  tocaste, el PR está incompleto.
- **No relajes un invariante para que pase un test.** Los cinco de arriba
  detectaron bugs reales (el `balance()` que sumaba rechazados, el fixture del
  POS con un dígito de menos, el orden del documento que se perdía al persistir).
- **Verificá contra los datos antes de afirmar.** Todo número de este archivo
  salió de correr algo, no de razonar. Las tres fórmulas de Wompi parecían
  cerradas con 9 filas y con 115 aparecieron los 8 casos de ±$0,01.
- **Si agregás una fuente**, tocá `sources.py` + un adapter. Si necesitás tocar
  `domain/` o `reconcile/`, pará: probablemente el modelo esté filtrando la
  fuente hacia adentro.
- **La cuenta de Wompi es productiva.** Solo GET. No toques la config de
  reportes ni la URL de eventos: es la integración real de Payana y la cuenta es
  compartida.
