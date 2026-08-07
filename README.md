# Conciliación contable — Alimentos Alcázar

Sistema que convierte fuentes heterogéneas (Wompi, extractos de Bancolombia,
Odoo) en una historia explicable del dinero, y responde:

> ¿Qué plata esperábamos recibir, qué llegó al banco, qué falta, qué está mal
> registrado en el ERP, y **por qué creemos eso**?

> **Estado:** en construcción. El dominio, la capa de ingesta y el calendario
> hábil están implementados y testeados. Los adapters concretos y los motores de
> conciliación se completan al conocer el layout real de cada fuente.

## Cómo correrlo

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

La suite se divide en dos, y cada una responde algo distinto:

```bash
pytest -m unit          # 106 — solo dominio, sin I/O. Milisegundos.
pytest -m integration   # 245 — pipeline real sobre fixtures congelados.
pytest                  # 351
```

**Ningún test toca la red.** No es una convención: `tests/conftest.py` bloquea la
creación de sockets, y un test que intente salir falla con un mensaje explícito.
Los tests de la API usan `httpx.MockTransport`.

Ingesta sin credenciales ni red, sobre los datos versionados en `data/raw/`:

```bash
conciliacion ingest bancolombia --offline
```

Para las fuentes de API hace falta `.env` (copiar de `.env.example`):

```bash
conciliacion ingest wompi
```

Qué fuentes hay registradas y con qué connector/adapter:

```bash
conciliacion sources
```

Estado de un ledger ya persistido:

```bash
conciliacion show wompi
```

Conciliación de flujo canal → banco:

```bash
conciliacion reconcile
```

Conciliación contra el libro contable de Odoo:

```bash
conciliacion ingest wompi_erp --desde 2025-01-01 --hasta 2026-12-31
conciliacion reconcile-erp wompi
```

Escribe las **dos salidas** en `data/out/`: el reporte legible para el CFO y el
JSON estructurado. Se generan del mismo resultado — si salieran por caminos
distintos podrían afirmar cosas distintas sobre el mismo hecho.

La CLI es el punto de entrada del pipeline, no la interfaz de usuario: dispara
la ingesta y regenera las salidas. Correr `ingest` dos veces no duplica nada.

### Interfaz web

Dos procesos. Primero la API, que sirve lo ya persistido:

```bash
uvicorn conciliacion.api.main:app --reload
```

Después el front, en otra terminal:

```bash
npm --prefix web install && npm --prefix web run dev
```

Abre en `http://localhost:5173`. Vite proxea `/api` al backend, así que el
código del front pide rutas relativas y es el mismo en desarrollo y producción.

Las vistas están pensadas para **verificar**, no para decorar, y hay una por
pregunta. Mezclar exploración con conciliación convierte las dos en un reporte
que nadie lee:

| Vista | Qué pregunta contesta |
|---|---|
| **Panorama** | ¿cómo viene todo? Veredicto, KPIs y estado de cada fuente |
| **Fuentes de datos** | ¿qué trae cada origen, de dónde salió el dato y está verificado? Wompi, Bancolombia y Odoo con el mismo formato de tabla. **No** muestra la conciliación: el estado es un aviso de una palabra |
| **Flujo · canal → banco** | ¿la plata que Wompi prometió llegó al banco? Una fila por conclusión del motor, con su confianza y su razonamiento completo |
| **Libro · contra Odoo** | ¿el ERP refleja lo que pasó? Dos corridas —una por ledger, contra su propia cuenta del plan— porque la llave de match no es la misma. Lidera con la conclusión agrupada: del lado banco hay 431 hallazgos y 415 son el mismo |

En la vista de flujo hay dos niveles de profundidad, porque son dos preguntas
distintas: el **acordeón** contesta de qué está hecho un giro (sus ventas), y el
**panel lateral** contesta por qué el motor concluye lo que concluye —regla,
ventana de búsqueda, cada ajuste con su origen (declarado vs. inferido),
alternativas descartadas y el `raw_ref` del crédito bancario.

En la del ERP el panel muestra **los dos identificadores** —el del movimiento
del ledger y el de la línea de Odoo, con el nombre del asiento— que es lo que el
enunciado pide para cada coincidencia. Cuando falta una punta, ese hueco es la
discrepancia. Las dos vistas exponen su informe en Markdown para el CFO desde la
misma barra de filtros; el JSON que consume el front es el mismo que consumiría
una IA contadora.

`raw_ref` es el puntero al byte del que salió el movimiento
(`data/raw/bancolombia/Extracto_Abril.pdf#pagina=2,y=680`), para poder abrir el
archivo original y verificarlo a mano.

**El front no calcula ni formatea plata.** Renderiza el campo `formatted` que
viene del backend; `cents` solo se usa para ordenar y colorear. Si formateara
por su cuenta, la web y el CLI podrían mostrar el mismo monto distinto, y el
sistema dejaría de tener una sola versión de la verdad
([ADR-0004](docs/adr/0004-explanation-como-objeto-de-dominio.md)).

### Datos y secretos

| Ruta | ¿Va a git? | Qué es |
|---|---|---|
| `.env` | no | credenciales |
| `.env.example` | sí | plantilla |
| `data/raw/` | sí | evidencia: lo que el sistema ingirió |
| `data/out/` | no | salidas generadas, descartables |
| `conciliacion.db` | no | base regenerable desde `data/raw/` |

`data/raw/` se versiona para que el repositorio corra recién clonado. Los
payloads de la API se archivan **ya redactados**: la allowlist descarta datos de
titulares de tarjeta antes de que toquen disco ([ADR-0006](docs/adr/0006-allowlist-de-pii.md)).

## Organización

```
src/conciliacion/
├── domain/         Modelo canónico. Python puro, cero dependencias.
│   ├── money.py        Money: entero en centavos, nunca float
│   ├── movement.py     Movement inmutable con ID determinista
│   ├── ledger.py       Ledger: movimientos de UNA cuenta, con signo
│   └── explanation.py  Explanation: por qué el sistema concluye lo que concluye
├── ingest/         Fase 1. Connector (cómo llegan los bytes)
│   ├── ports.py        + Adapter (qué significan). Ejes ortogonales.
│   ├── registry.py     Registro de fuentes y pipeline de ingesta
│   ├── connectors/     LocalFile, HttpApi, Webhook, OdooRpc
│   └── adapters/       Uno por layout
├── reconcile/
│   ├── calendar.py     Días hábiles Colombia (Ley Emiliani). Necesario para T+1.
│   ├── flow/           Fase 2. Canal → banco
│   │   ├── engine.py       el matcher; produce Explanation por conclusión
│   │   └── findings.py     estados, cobertura, reporte
│   └── erp/            Fase 3. Ledger → libro de Odoo
├── storage/        SQLite, un archivo, sin ORM
├── report/         Un contrato, dos proyecciones
│   ├── contract.py     la representación serializable; nadie más calcula
│   ├── cfo.py          Markdown para el humano
│   └── flow_views.py   proyección de la conciliación
├── api/            FastAPI delgada: sirve resultados guardados
└── cli.py

data/raw/           Evidencia cruda, tal como llegó. Inmutable.
data/out/           Reportes generados. Descartable.
docs/adr/           Decisiones de diseño
tests/
```

## Los dos usuarios

El motor produce un único `Explanation` estructurado por conclusión, y de ahí
salen **dos proyecciones**:

- **CFO** → reporte Markdown legible.
- **IA contadora** → JSON con `rule_id` estables, movimientos citados por ID,
  ajustes marcados como observados / inferidos / prorrateados, y las
  alternativas descartadas con su motivo.

Ninguna de las dos calcula nada: si divergieran, el sistema afirmaría dos cosas
distintas sobre el mismo hecho. Ver [ADR-0004](docs/adr/0004-explanation-como-objeto-de-dominio.md).

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre en cada PR:

| Job | Qué verifica |
|---|---|
| `test (3.11)` · `test (3.13)` | ruff + unit + integration + cobertura |
| `smoke` | **que el repo corra recién clonado** |

Tres checks, no siete. `unit` e `integration` van como **steps** dentro del mismo
job: la suite entera tarda ~9 s, así que un runner extra costaría más setup que
los tests que ahorra, y como steps se conserva igual la señal de cuál rompió.
`ruff` corre una sola vez — su resultado no depende de la versión de Python.

El job `smoke` es el que más aporta: instala desde cero sin credenciales, corre
la ingesta offline, verifica que salgan 426 movimientos, la vuelve a correr y
exige `0 nuevos` para probar la idempotencia. Falla también si alguien commitea
un `.env`.

Es lo que sostiene la afirmación de este README de que el repositorio funciona
recién clonado: si alguien introduce una dependencia oculta a `.env` o a la API
de Wompi en el camino offline, los tests seguirían pasando y este job no.

## Para agentes de IA

[`CLAUDE.md`](CLAUDE.md) tiene el contexto que **no se deduce leyendo el código**:
hechos verificados sobre los datos reales, trampas conocidas, invariantes que no
se pueden romper y convenciones. Es el archivo a leer antes de tocar nada.

## Decisiones de diseño

| ADR | Decisión |
|---|---|
| [0001](docs/adr/0001-modelo-canonico.md) | Movement inmutable, ID determinista, `Money` entero |
| [0002](docs/adr/0002-connector-vs-adapter.md) | Connector ⟂ Adapter: N+M clases, no N×M |
| [0003](docs/adr/0003-sqlite-como-persistencia.md) | SQLite stdlib sin ORM; crudos en archivos |
| [0004](docs/adr/0004-explanation-como-objeto-de-dominio.md) | La explicación es estructura, no texto |
| [0005](docs/adr/0005-tarifario-wompi.md) | El tarifario valida, no es fuente. `DECLARED` 9/9 vs `INFERRED` 47/55 |
| [0006](docs/adr/0006-allowlist-de-pii.md) | Allowlist de campos en el borde: falla cerrada |
| [0007](docs/adr/0007-adapter-extracto-bancolombia.md) | PDF por coordenadas, clave sintética con saldo, autovalidación |
| [0008](docs/adr/0008-reparto-disjunto-entre-fuentes.md) | Tres fuentes, `MovementKind` disjuntos, el ledger cierra en cero |
| [0009](docs/adr/0009-extensibilidad-demostrada-pos.md) | Costo de sumar el POS, medido: 0 líneas de dominio, cadencia = 1 línea de config |
| [0010](docs/adr/0010-conciliacion-de-flujo.md) | Flujo: dos saltos, el primero declarado. "Falta plata" ≠ "falta data" |
| [0011](docs/adr/0011-conciliacion-contra-el-erp.md) | ERP: el libro es otro ledger. Se define por cuenta, no por diario |

## Cómo leer la salida

`conciliacion reconcile` produce, sobre los datos del challenge:

```
matched            55        exact     3
out_of_coverage     1        high     52
unmatched_bank      3        medium    4

conciliado   $263.600.926,28
en disputa       $894.105,90
redondeo               $0,08
```

Las cuentas cierran por ambos lados sin residuo:

- `55 conciliados + 3 huérfanos = 58` líneas de Wompi en el extracto
- `55 conciliados + 1 fuera de cobertura = 56` desembolsos de la API

**`out_of_coverage` no es un faltante.** Un giro sin crédito bancario y un giro
cuyo extracto no bajamos se ven idénticos en los datos y significan lo
contrario. El primero es una alerta; el segundo, una nota al pie. Mezclarlos
haría que el CFO vea faltantes que no existen.

**La confianza no es decorativa.** Los 3 `exact` son los desembolsos cuyo
desglose declaró el canal; los 52 `high` usan la fórmula inferida, con un error
medido de un centavo por venta. Esos centavos aparecen como `redondeo` —
separados de lo que está realmente en disputa, para que el total del veredicto
coincida con la suma del detalle.

Cada conclusión trae qué movimientos relaciona, de dónde salió cada ajuste
(declarado o estimado), qué ventana temporal consideró y qué alternativas
descartó. Ver [ADR-0010](docs/adr/0010-conciliacion-de-flujo.md).

## Agregar una fuente nueva: el POS, medido

El enunciado pide mostrar cuánto código nuevo hace falta para sumar el POS
bancario. Está hecho de verdad, no descrito: `pos_asobancaria.py` es un adapter
real y testeado que corre por el mismo pipeline que Wompi y Bancolombia.

```bash
pytest tests/test_extensibilidad_pos.py -v
```

| Qué se escribió | Líneas |
|---|---|
| `ingest/adapters/pos_asobancaria.py` | ~128 de código (+43 de docstring del layout) |
| Fixture | 5 |
| Registro de la fuente | 8 |
| Cadencia T+2 | **1** |

| Qué **no** se tocó | Líneas |
|---|---|
| `domain/` | 0 |
| `reconcile/` | 0 |
| `storage/` | 0 |
| `ingest/ports.py`, `ingest/registry.py`, `ingest/connectors/` | 0 |

Tres resultados:

1. **Cero connector nuevo.** Formato nuevo sobre transporte conocido: reusa el
   `LocalFileConnector` de los PDF y los CSV. Es para lo que sirve separar
   Connector de Adapter.
2. **Cero dominio.** El POS no agregó ningún `MovementKind` ni campo.
   `test_el_dominio_no_cambio` falla si un canal futuro necesitara uno propio.
3. **La cadencia distinta es una línea de config.** El POS liquida T+2 y Wompi
   T+1; eso es un entero en `SETTLEMENT_POLICIES`, no una rama en el motor. Es
   la parte que realmente prueba algo: un formato nuevo lo resuelve cualquier
   diseño con interfaces.

**Qué es sintético:** los datos del fixture — el POS está fuera del alcance del
challenge y no hay archivos reales. **Qué no:** el layout. `Asobancaria 2001` es
un formato bancario colombiano de ancho fijo, ofrecido por el propio dashboard de
Wompi como alternativa al CSV.

El canal **no está registrado** en `sources.py` ni en `config.ACCOUNTS`, para no
meter datos sintéticos en el pipeline productivo. Se registra en su test.
Detalle completo en [ADR-0009](docs/adr/0009-extensibilidad-demostrada-pos.md).
