# ADR-0006 — Allowlist de campos en el borde de ingesta

**Estado:** aceptado
**Fase:** 1

## Contexto

`GET /transactions` de Wompi devuelve, junto con los datos contables, esto:

| Campo | Contenido |
|---|---|
| `customer_email` | mail del comprador |
| `customer_data` | nombre completo, teléfono, `device_id`, user-agent, resolución de pantalla |
| `payment_method.extra` | BIN, últimos cuatro dígitos, marca, **nombre del tarjetahabiente**, datos 3DS |
| `shipping_address` | dirección |
| `payment_source_id`, `redirect_url` | identificadores de medio de pago guardado |

Son personas reales. Y [ADR-0003](0003-sqlite-como-persistencia.md) establece que
los payloads crudos se persisten en `data/raw/` como evidencia — que en este
repositorio además se versiona.

Aplicado sin más, eso significa **escribir datos de titulares de tarjeta a disco
y commitearlos a GitHub**.

## Decisión

El connector proyecta cada respuesta sobre una **allowlist** *antes* de archivar
y *antes* de emitir el `RawRecord`. Los campos fuera de la lista nunca tocan
disco ni memoria del pipeline.

Campos que sobreviven en `/transactions`:

```
id, created_at, finalized_at, amount_in_cents, currency, reference,
payment_method_type, status, status_message, disbursement,
bill_id, payment_link_id
```

`payment_method_type` (`"CARD"`, `"BANCOLOMBIA_QR"`) sí entra: es el tipo, no la
tarjeta, y el tarifario está indexado por él ([ADR-0005](0005-tarifario-wompi.md)).

La proyección es **recursiva**: `bank_account_number` viaja anidado dentro de
`disbursement`, y una proyección que solo mire el primer nivel lo deja pasar.

## Allowlist, no denylist

Es la parte que importa.

Una denylist enumera lo prohibido. Si Wompi agrega un campo con PII el mes que
viene, la denylist **lo persiste en silencio** y nadie se entera hasta que alguien
lee un JSON commiteado.

La allowlist enumera lo permitido. Un campo nuevo simplemente no entra, aunque
nadie actualice el código. **Falla cerrada.** En una decisión sobre datos
personales, ese es el único modo de falla aceptable.

Fijado en `test_un_campo_nuevo_desconocido_no_pasa`.

## Por qué no es solo privacidad

Un sistema de conciliación contable **no tiene por qué tocar datos de tarjeta**.
Guardar BIN + últimos cuatro + nombre del tarjetahabiente es exposición
regulatoria (ámbito PCI-DSS) a cambio de cero valor: para responder *"¿esta venta
llegó al banco?"* alcanzan el id, el monto, la fecha, el estado y el desembolso
asociado.

Es minimización de datos: no se ingiere lo que no se necesita. La allowlist no
es una capa de protección sobre datos que igual entran — es la decisión de que
no entren.

## La tensión con "raw = evidencia byte a byte", y cómo se resuelve

[ADR-0002](0002-connector-vs-adapter.md) sostiene que `RawRecord.locator` debe
apuntar a la evidencia original, para que cualquier conclusión se rastree hasta
el byte del que salió. Archivar una versión redactada debilita eso.

Se resuelve redefiniendo qué es la evidencia: **la evidencia es lo que el sistema
ingirió**, no lo que el proveedor emitió. El sistema declara explícitamente qué
campos descarta en el borde y por qué; esa declaración es parte del contrato de
ingesta, versionada y testeada. La cadena de trazabilidad queda intacta para todo
lo que el sistema efectivamente usa — y no puede haber una conclusión apoyada en
un campo que nunca entró.

Los registros quedan marcados con `metadata["redacted"] = True` para que la
redacción sea visible desde el reporte y no un supuesto tácito.

## Cómo se verifica

El test decisivo no revisa la lista de campos: construye una transacción con la
forma real de la API —con PII en todos los campos que la API realmente pobla— y
recorre **lo emitido y lo escrito a disco** buscando cada cadena sensible.

Corrido contra la API productiva sobre 157 transacciones reales, auditando el
archivo resultante:

```
customer_email 0 · customer_data 0 · card_holder 0 · last_four 0
full_name 0 · phone_number 0 · device_id 0 · bank_account_number 0 · "@" 0
```

El último término es el que vale: un `@` suelto atraparía cualquier mail que se
hubiera colado por un campo no previsto.

## Alternativas descartadas

- **Payload completo + `data/raw/` en .gitignore.** Mantiene la evidencia
  intacta, pero el repositorio deja de correr recién clonado: quien evalúa vería
  tests que dependen de credenciales de una API productiva a la que no tiene
  acceso. Y los datos de tarjeta seguirían en el disco de cualquiera que corra
  la ingesta.
- **Hashear la PII en vez de descartarla.** Un hash de un mail sigue siendo un
  identificador de una persona, y no aporta nada a la conciliación.
- **Redactar en el adapter en vez del connector.** Demasiado tarde: el connector
  ya archivó. La redacción tiene que ocurrir en el punto donde los datos entran
  al sistema, que es exactamente el connector.
