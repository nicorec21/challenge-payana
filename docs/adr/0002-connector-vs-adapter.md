# ADR-0002 — Separar Connector de Adapter en la capa de ingesta

**Estado:** aceptado
**Fase:** 1 (Modelado e Ingesta)

## Contexto

El challenge pide que sumar una fuente nueva sea barato, y aclara que dos cosas
varían de forma independiente:

- **el formato**: PDF layout A, PDF layout B, JSON de API REST, webhook, Excel, CSV
- **el tipo de conexión**: llamada a API, parseo de archivo local, recepción de webhook

La tentación es modelar una sola abstracción `DataSource` que sepa traer los
datos y también interpretarlos.

## Decisión

Dos puertos separados ([`ingest/ports.py`](../../src/conciliacion/ingest/ports.py)):

| Puerto | Responsabilidad | Ejemplos |
|---|---|---|
| `Connector` | traer bytes crudos, sin interpretarlos | `LocalFile`, `HttpApi`, `Webhook`, `OdooRpc` |
| `Adapter` | traducir UN layout al modelo canónico | `BancolombiaPdfLayoutA`, `WompiApiJson` |

En el medio viaja `RawRecord`: el payload crudo más un `locator` que apunta al
byte de origen.

## Justificación

Formato y transporte son **ejes ortogonales**, y una sola abstracción los
multiplica. Con N formatos y M transportes, un `DataSource` fusionado cuesta
N×M clases; separados cuestan N+M.

No es teórico en este dominio:

- Bancolombia cambia el layout del PDF → 1 adapter nuevo, mismo connector.
- Wompi pasa de export manual a webhook → 1 connector nuevo, mismo adapter (el
  JSON del webhook y el de la API comparten forma).
- Mañana el mismo PDF llega por SFTP en vez de descarga manual → 1 connector,
  cero adapters.

Beneficio secundario: el `Adapter` no hace I/O, entonces se testea con un
fixture en memoria. Sin este split, testear un parser requiere mockear red o
filesystem — y los parsers son justo la parte que más casos borde tiene.

## Mecanismo de selección: `sniff`

Un `SourceSpec` acepta **varios** adapters y el pipeline elige por contenido
(`Adapter.sniff(record)`), no por configuración del usuario. Resuelve el caso
"el banco cambió el layout a mitad de año" sin que el operador tenga que saber
qué archivo es de qué layout.

La precedencia es **el orden de registro**, explícito, no un score de
confianza. Un empate resuelto por score es un bug silencioso: dos parsers que
producen movimientos distintos del mismo archivo y nadie se entera.

## Costo de sumar el POS (lo que pide el challenge)

1. Un `Adapter` con `sniff` + `parse` (~60-100 líneas según el formato).
2. Un `Account` y un `SourceSpec` en el registry (~5 líneas).
3. Reusar `LocalFileConnector` o `HttpApiConnector` si aplica: 0 líneas.

El motor de conciliación de flujo **no se toca**: opera sobre `role="channel"`
genérico, no sobre "Wompi". Lo único que puede requerir código es una cadencia
de settlement distinta a T+1, y eso es un parámetro de la regla, no una regla nueva.

## Consecuencias

- **Contra:** dos conceptos donde un principiante ve uno; un `RawRecord`
  intermedio que en el caso simple parece burocracia.
- **A favor:** el costo marginal de una fuente es sublineal, y los parsers —la
  parte frágil— quedan puros y testeables.

## Alternativas descartadas

- **`DataSource` único.** Más simple con 2 fuentes, N×M con 5. El challenge
  evalúa explícitamente el costo de extender.
- **Adapters declarativos (mapeo por config YAML).** Atractivo hasta el primer
  PDF con encabezado repetido por página y montos con signo al final. El
  parseo de extractos bancarios es irreductiblemente imperativo.
