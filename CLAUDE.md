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

Este archivo y el README afirman cantidades concretas: 364 tests, 106/258,
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
pytest                                        # 364
pytest -m unit                                # 106 — solo dominio, milisegundos
pytest -m integration                         # 258 — pipeline sobre fixtures
ruff check .
conciliacion ingest bancolombia --offline     # sin credenciales
conciliacion ingest wompi                     # requiere .env
conciliacion sources --offline
conciliacion show wompi
conciliacion reconcile                        # flujo canal -> banco, 2 salidas
conciliacion ingest wompi_erp --desde 2025-01-01 --hasta 2026-12-31
conciliacion reconcile-erp wompi              # ledger vs libro de Odoo
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

El 9/9 de `DECLARED` **está fijado por test**: `audit()` del adapter del CSV
contrasta cada descuento declarado contra `WOMPI_FEES` y da cero avisos sobre los
4 archivos. Si empieza a avisar, o cambió la tarifa real o se rompió una fórmula
— y la CLI lo muestra en la ingesta. Ver ADR-0012.

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

**Los 4 extractos NO encadenan entre sí.** Cada uno es internamente consistente
—los tres invariantes pasan— pero el cierre de un mes no es la apertura del
siguiente:

```
2026-01 cierra  451.395.844,00
2026-02 abre    457.662.321,55    salto  +6.266.477,55
2026-03 abre    417.484.065,05    salto −201.370.697,42
2026-04 abre    284.557.304,49    salto −149.523.727,17
```

No hay huecos de fecha entre períodos, así que es data sintética generada mes a
mes sin encadenar. Dos consecuencias:

- **No agregar un invariante de continuidad entre extractos**: fallaría sobre
  los datos provistos y no indicaría un bug del sistema.
- **`Ledger.balance()` del banco NO es un saldo de cuenta.** Es la suma de los
  movimientos ingeridos, o sea el flujo neto del período. Coincide con el saldo
  real solo si se ingirió desde la apertura de la cuenta. Para Wompi sí es un
  saldo con sentido (plata cobrada y no girada); para el banco no. La interfaz
  lo etiqueta distinto según el `role` por eso.

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

**Los adapters no corrigen: auditan.** Un adapter puede implementar el protocolo
opcional `Auditor` (`audit(record) -> Iterator[str]`) y contrastar lo que leyó
contra las reglas de `config.py`. El dato de la fuente **se ingiere igual**: la
fuente es la verdad, la config es la hipótesis. Los avisos van a
`IngestionReport.warnings` y **no tumban `report.ok`** — un cambio de tarifa y un
PDF corrupto se arreglan al revés, así que no pueden verse igual.

Va aparte de `parse` por tres razones, y la del medio es la que sorprende:
traducir no es juzgar; el aviso **no se persiste** (`Movement` es inmutable, así
que un desvío guardado en `metadata` seguiría afirmándose después de corregir la
config); y un desvío puede no tener movimiento donde colgarse —si una retención
baja a cero la fila no emite movimiento, y esa desaparición es justo lo que hay
que avisar—. Ver ADR-0012.

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

**Fase 1 completa.** Modelo, ingesta, persistencia, CLI, extensibilidad medida.

**Fase 2 (flujo canal→banco): COMPLETA.** Motor, persistencia, CLI, API, vista y
ADR-0010. Sobre los datos del challenge: 55 conciliados, 3 créditos huérfanos, 1
fuera de cobertura; las cuentas cierran por ambos lados sin residuo.

Decisiones que conviene no revertir sin leer ADR-0010:

- **El primer salto (venta → desembolso) NO se busca**: el canal declara el
  `disbursement_id`. No hay subset-sum, y es deliberado: la ambigüedad que
  advierte el enunciado no aplica por esta vía.
- **`UNMATCHED_SETTLEMENT` ≠ `OUT_OF_COVERAGE`.** Se ven idénticos y significan
  lo contrario. `is_problem` es `False` para el segundo.
- **`disputed_amount` ≠ `unexplained_total`.** El segundo incluye el redondeo de
  matches exitosos. Mezclarlos hace que el veredicto no coincida con el detalle.
- **La cobertura es un parámetro**, no una derivación. Derivarla del rango de
  movimientos es conservador pero inutilizable en ledgers chicos.

Lo que quedó apoyando la fase:
- `disbursement_id` declarado por Wompi en cada transacción ⇒ agrupar pagos en su
  liquidación es un `GROUP BY`, no una búsqueda de subconjuntos. **La ambigüedad
  que advierte el enunciado no aplica por esta vía.**
- 10/10 desembolsos verificados contra créditos bancarios.
- El error de la inferencia, medido.
- `SettlementPolicy` para generar candidatos.
- `IngestionReport.requested_window` para distinguir *"no llegó la plata"* de
  *"no tengo datos de ese período"* — que se ven idénticos y significan lo opuesto.

**Fase 3 (ERP/Odoo): COMPLETA.** Connector XML-RPC, adapter, motor, CLI, API y
ADR-0011. Resultado sobre los datos: el ERP registra el **24,7%** de los
movimientos de Wompi y el **2,6%** de los del banco.

Decisiones que conviene no revertir sin leer ADR-0011:

- **El libro se define por CUENTA, no por diario.** Tomar el diario 48 como
  libro de Wompi perdería 13 de 53 líneas, incluidos los 11 giros al banco.
- **La llave de match se decide contando**, no con lista negra: una referencia
  que se repite no identifica nada.
- **Se recorre el LIBRO, no el ledger.** Una referencia identifica una
  *transacción* (hasta 5 movimientos), no un movimiento. Al revés, la comisión
  se lleva la línea de la venta — fue un bug real con 4 falsos positivos.
- **`draft`/`cancel` son un estado propio** (`NOT_POSTED`), ni coincidencia ni
  ausencia.

Detalle de lo que hay adentro del ERP, abajo.

---

## Odoo: lo que hay adentro

Explorado, **nada implementado**. Odoo 18. Diarios: 48 = Wompi, 49 = Bancolombia.

### La cuenta puente resuelve partida doble → partida simple

```
diario 48 "Wompi Tarjetas"  →  cuenta default 1110001 Wompi Tarjetas
diario 49 "Bancolombia"     →  cuenta default 111001 Bank
```

Asiento de venta (48):          Asiento de acreditación (49):
```
1110001  DEBE   243.698,00      111001   DEBE   1.327.369,53
420500   HABER  243.698,00      1110001  HABER  1.327.369,53
```

**`1110001 Wompi Tarjetas` ES el ledger `wompi` como cuenta contable.** El giro
aparece **una sola vez** —en el diario 49, moviendo plata de la cuenta puente al
banco—, así que no hay doble conteo entre diarios.

**Regla de proyección**, sin casos especiales:

```
monto_con_signo = Σ (debit − credit) sobre las líneas cuya account_id sea la del ledger
```

Ambas cuentas son de tipo `bank`: debe = entrada, haber = salida. Es exactamente
la convención de signos del modelo. Verificado:

| Modelo | Odoo |
|---|---|
| `PAYMENT +243.698` (wompi) | 1110001 DEBE 243.698 |
| `SETTLEMENT −1.327.369,53` (wompi) | 1110001 HABER 1.327.369,53 |
| `BANK_CREDIT +1.327.369,53` (banco) | 111001 DEBE 1.327.369,53 |

### `ref` es la llave de match del lado Wompi

`account.move.ref` del diario 48 es la referencia de la transacción de Wompi
**en mayúsculas** (`TKFGJOKOQFHWVIGU71QQQ` ↔ `tkfgjokoqfhwvigu71qqq`).
40 de 40 matchean por ref, **ninguna con monto distinto**.

Del lado banco `ref = "Acreditación Wompi"` en todas: descriptivo, no llave. Ahí
hay que matchear por monto + fecha.

### 🔴 Las cuentas del enunciado NO se usan

Los diarios 48 y 49 solo tocan `1110001`, `420500`, `111001` (y una vez
`111002 Suspense`). **Ni comisión, ni IVA, ni retención.** Y los códigos no se
llaman como dice el enunciado:

| Código | Nombre real | Líneas en todo Odoo |
|---|---|---|
| 530505 | **Currency Exchange Loss** (no "Gastos Bancarios") | 1 |
| 236500 | Withheld at source | 1 |
| 240810 | Discountable VAT | 286, fuera de estos diarios |

El bruto entra a `1110001`, el neto sale, y la diferencia —las comisiones— queda
como saldo permanente en la cuenta puente, sin llevarse nunca a gasto.

**Consecuencia medible, y la razón de `ERP_UNREPRESENTABLE_KINDS` en
`config.py`:** los `FEE` y `TAX` del ledger **nunca** van a aparecer en
`1110001`, por más completo que esté el ERP. Un solo porcentaje de cobertura
junta dos problemas que se arreglan al revés:

```
                     matched  total          se arregla…
comparable              49     171   28,7%   asentando lo que falta
sin cuenta donde ir      0      27           rediseñando el plan de cuentas
                                             (−$127.131,96 en comisiones e IVA)
global                  49     198   24,7%   ← no dice cuál de las dos
```

Por eso `ErpReport.coverage()` los separa y `coverage_ratio()` quedó como el
global de una línea. **La exclusión sale de config, no de contar ceros**: que un
tipo dé cero coincidencias puede ser casualidad; que no exista la cuenta es un
hecho del plan contable y afirmarlo requiere haberlo mirado. Lo fijan
`test_la_cobertura_separa_lo_que_no_tiene_cuenta_donde_asentarse` y
`test_no_se_excluye_un_tipo_solo_porque_dio_cero`.

Del lado `bancolombia` no hay tipos sin cuenta: su 2,6% es cobertura real.

**La ambigüedad del enunciado, resuelta.** Dice *"las cuentas contables **a
utilizar** son"*. Como descripción es falsa —la tabla de arriba lo prueba—, así
que se lee como **instrucción de lo que hay que proponer**. Eso es
`ERP_PROPOSED_ACCOUNTS` en `config.py`: `FEE → 530505`, `TAX → 240810 + 236500`.
`TAX` mapea a dos porque el modelo agrupa lo que el plan separa (IVA descontable
vs. retención por cobrar).

Es el complemento de `ERP_UNREPRESENTABLE_KINDS`: ese dice *qué* no tiene dónde
asentarse, este *dónde debería ir*. Reportar 27 movimientos sin cuenta y no
nombrar ninguna deja el trabajo a medias, así que `coverage()` emite
`unrepresentable_proposed_accounts` y el reporte del CFO tiene su propia sección
—separada de los faltantes, porque se arreglan por vías distintas: contabilizar
vs. rediseñar el plan—.

`ODOO_ACCOUNT_REALITY` guarda el nombre real de cada código propuesto. **No se
propone una cuenta sin haber mirado qué es en Odoo**: repetir el nombre del
enunciado como si describiera la instancia es exactamente el error que este mapa
vino a corregir. Lo fija `test_no_se_propone_una_cuenta_sin_haber_mirado_que_es_en_odoo`.

### El ERP está incompleto — y eso es el entregable de Fase 3

```
115 ventas aprobadas  vs  40 asientos (diario 48)  →  75 sin registrar
 58 acreditaciones     vs  12 asientos (diario 49)  →  47 sin registrar
```

De las 40 que existen: coincidencia perfecta, cero diferencias de monto.

El modelo es **más granular** que el ERP (separa PAYMENT / FEE / TAX /
SETTLEMENT donde Odoo registra solo bruto y neto). Esa diferencia no es un
problema del modelo: es una discrepancia a reportar.

### La instancia de Odoo es compartida y tiene basura de prueba

Igual que la cuenta de Wompi. Sobre las cuentas `111001` y `1110001` hay
asientos que **no son operación de Alimentos Alcázar**:

```
2025-11-19  (sin nombre)       Miscellaneous       $0,00     draft
2025-12-29  MISC/2025/12/0001  Miscellaneous  -$20.000,00    "Write-Off"
2025-12-29  MISC/2025/12/0002  Miscellaneous  $100.000,00    "Write-Off"
2026-04-20  BILL 212           Vendor Bills    $10.000,00    "suelo"
2026-06-11  BNK8/2026/00012    Bancolombia     $36.890,00    "akjshdjkasd"
2026-05-26  (sin nombre) ×2    Miscellaneous  $0,00 y $100   cancel
```

Son los 4 `MISSING_IN_LEDGER` del banco y los 3 `NOT_POSTED` de los dos lados.
**Los veredictos son correctos**: el ERP registra algo que ningún movimiento
respalda, que es exactamente la discrepancia que el enunciado pide señalar. No
hay que silenciarlos.

**Los 2 `MISSING_IN_LEDGER` del lado Wompi NO son basura** —no los metas en la
misma bolsa—. Son `BNK8/2026/00002` ($257.940,85 el 06/01) y `BNK8/2026/00007`
($257.359,77 el 02/02): acreditaciones que Odoo registra y ningún giro de Wompi
respalda. La Fase 2 señala **los mismos dos montos** como créditos bancarios
huérfanos, por un camino completamente distinto. Dos motores independientes
apuntando al mismo par es la señal más fuerte que produce el sistema.

**Trampa asociada:** seis de esos siete —todos menos `BILL 212`— caen fuera del rango del ledger
operativo, porque el libro se ingiere ancho a propósito (`--desde 2025-01-01
--hasta 2026-12-31`) y los extractos son solo 4 PDF de ene–abr 2026. Es tentador
leer eso como *"falta cobertura"* y marcarlos `OUT_OF_COVERAGE` —el estado existe
en `ErpStatus` y **hoy no se emite nunca**—. **No lo hagas sin mirar cada línea:**
sobre estos datos escondería hallazgos reales. El hueco del motor es real como
propiedad general (ingerir solo abril inundaría de falsos positivos), pero acá no
está produciendo ninguno.

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
| 0012 | El tarifario audita la ingesta; el aviso no se persiste |

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
