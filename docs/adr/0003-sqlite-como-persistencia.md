# ADR-0003 — SQLite (stdlib, sin ORM) + payloads crudos en archivos

**Estado:** aceptado

## Contexto

El challenge deja la infraestructura como lienzo en blanco. Las opciones reales
eran: todo en memoria, archivos JSON, o SQLite.

## Decisión

- **Canónico y resultados** → SQLite, un archivo, vía `sqlite3` de la stdlib.
- **Payloads crudos** → archivos en `data/raw/`, tal como llegaron.
- **Reportes** → artefactos generados en `data/out/`, descartables.
- **Sin ORM.**

## Por qué SQLite y no JSON

SQLite **es** un archivo. No hay servidor, no hay daemon, no hay dependencia:
`sqlite3` viene en la stdlib. La elección no es "liviano vs pesado", es "un
archivo sin garantías vs un archivo con garantías". Las garantías que importan acá:

1. **Idempotencia declarada, no programada.** `UNIQUE(source_id, external_id)`
   la hace cumplir el motor. En JSON depende de que el código de ingesta se
   acuerde de chequear; el día que se olvida, entran duplicados silenciosos y
   la conciliación cierra mal sin fallar. Es el bug más caro posible acá.
2. **Escritura atómica.** Un dump de JSON completo interrumpido a la mitad deja
   el archivo corrupto y se pierde todo.
3. **La query central del matcher** es "movimientos del ledger X entre D y D+2
   con status aprobado", y corre una vez por día del período. Con índice es
   directa; en JSON es cargar todo y filtrar en Python en cada iteración.
4. **Historial de corridas.** Guardar varias `reconciliation_run` para comparar
   el efecto de un cambio de regla es una tabla. En JSON es inventar una
   convención de carpetas.

## Por qué los crudos NO van a la base

Los PDF y JSON originales son **la evidencia**. Se quieren poder abrir, diffear
y adjuntar a un mail. Como BLOB dejan de ser inspeccionables, y el `locator` de
`RawRecord` (`data/raw/banco/2025-03.pdf#pagina=2,linea=17`) deja de ser una
ruta real que alguien puede seguir. La cadena de explicabilidad tiene que llegar
hasta un archivo que el CFO pueda abrir.

## Por qué sin ORM

Son ~5 tablas y las consultas son agregaciones por fecha y rango de monto —
justo donde un ORM estorba. SQLAlchemy sumaría una dependencia pesada y un
modelo mental entero (sesiones, identity map, lazy loading) a cambio de nada
que no resuelva un `SELECT`. El repositorio es un puerto delgado; si el volumen
justificara Postgres, se cambia la implementación sin tocar el dominio.

## Persistencia híbrida de la `Explanation`

En la tabla `finding`, la `Explanation` se serializa como JSON en una columna, y
los campos que se consultan (`rule_id`, `status`, `confidence`, `unexplained`)
salen a columnas indexadas.

Motivo: la forma de la explicación depende de la regla y va a cambiar mientras
se afinan las reglas; normalizarla en tablas obligaría a migrar el esquema en
cada iteración. Los campos que se filtran son estables y pocos, así que se
promueven. Híbrido a propósito.

## Consecuencias

- **Contra:** hay SQL a mano y una capa de mapeo fila↔dataclass; el JSON
  embebido no es consultable con SQL (aceptable: no se filtra por ahí).
- **A favor:** cero dependencias de persistencia, garantías reales, `.db`
  borrable para reproducir desde cero.

## Alternativas descartadas

- **Todo en memoria.** Suficiente para el volumen del challenge, pero el
  enunciado marca persistencia como bonus y sin ella se pierde el historial de
  corridas, que es lo que hace comparable un cambio de regla.
- **Postgres/Docker.** Introduce un paso de infra que el operador tiene que
  levantar para correr una herramienta que procesa miles de filas. El README
  pasaría de "corré esto" a "instalá Docker".
