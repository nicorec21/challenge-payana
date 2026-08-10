# ADR-0013 — Offline por replay del archivo; la salida versionada se verifica en CI

**Estado:** aceptado
**Fase:** entregable (cierra el entregable 9 del enunciado)

## Contexto

El enunciado pide como entregable *"la salida del sistema sobre los datos
provistos: reporte legible para el CFO y salida estructurada para la IA"*. Hasta
este cambio esa salida existía solo en `data/out/`, que no se versiona: quien
clonaba el repositorio tenía que correr el pipeline para verla.

Y no podía: `--offline` **omitía** las fuentes de red en vez de reproducirlas.
Un clon fresco reconstruía Bancolombia (PDF) y los descuentos (CSV), pero no el
ledger de Wompi (API) ni los libros de Odoo (XML-RPC) — así que `reconcile` no
tenía contra qué correr. La promesa del docstring de `build_registry`
("reprocesar desde lo ya archivado") estaba escrita y no implementada, el mismo
patrón que ADR-0012 encontró con el tarifario.

La materia prima ya estaba versionada: los connectors de red archivan cada
página **ya redactada** en `data/raw/` como evidencia (ADR-0006), y esos
archivos van a git. Faltaba el camino de vuelta.

## Decisión

**1. `ArchiveReplayConnector`: el archivo es reproducible.**

En modo offline, las fuentes de red no se omiten: se registran con el mismo
nombre y los mismos adapters, pero con un connector que relee las páginas
archivadas y reemite un `RawRecord` por ítem — mismos payloads, misma
`metadata` que gobierna el `sniff`, y **el mismo `locator`** que produjo el
connector de red. Como el id de `Movement` es determinista (ADR-0001) y el
`source_id` viene del adapter, ambos caminos producen ledgers idénticos
movimiento a movimiento. No es una aspiración: la corrida offline completa
reproduce las seis salidas **byte a byte** (solo difiere `generated_at`).

El replay ignora la `FetchWindow`: el archivo *es* la ventana que se pidió al
capturarlo, y filtrar acá inventaría una captura que nunca ocurrió.

**2. `docs/salida/` versiona la salida; CI la fija.**

Las seis salidas sobre los datos del challenge (`.md` CFO + `.json` IA, flujo y
ERP×2) se versionan en `docs/salida/`. El job `smoke` — que ya probaba que el
repo corre recién clonado — ahora reconstruye todos los ledgers por replay,
corre las tres conciliaciones sin red, y compara lo generado contra la copia,
ignorando solo `generated_at`. Un cambio que mueve la conciliación sin
actualizar la copia falla con el archivo señalado.

Es la conversión de una regla de documentación ("actualizá la salida en el
mismo cambio") en un invariante ejecutable, que es lo que este repo hace con
todo lo que afirma.

## Alternativas descartadas

- **Versionar `conciliacion.db`.** Versiona un derivado binario no diffeable y
  no es lo que pide el entregable. La base se reconstruye desde `data/raw/` —
  eso es exactamente lo que el replay hace cierto.
- **Fixtures propios para el replay.** Duplicaría la evidencia: los payloads
  archivados ya son los datos reales, redactados y versionados. Un fixture
  aparte podría divergir de lo que la ingesta en vivo produce.
- **Seguir omitiendo las fuentes de red offline.** Deja el entregable 9 a
  medias y hace inverificable la copia versionada: sin replay, CI no puede
  regenerar la salida para compararla.

## Consecuencias

- La ingesta vía API web (`api/main.py`) sin credenciales ahora sirve las
  fuentes de red por replay; el badge de la interfaz las muestra como
  «archivo local», que pasó a ser verdad.
- Actualizar la evidencia (reingerir en vivo y commitear `data/raw/`) puede
  mover las salidas: el mismo check obliga a actualizar `docs/salida/` en ese
  cambio, que es el comportamiento deseado.
- El replay no escribe: `data/raw/` sigue siendo evidencia de solo-apéndice.
