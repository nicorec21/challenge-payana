# Salida del sistema sobre los datos del challenge

Copia versionada de lo que escribe la CLI en `data/out/`, para leer sin correr
nada. Cada conciliación produce dos proyecciones del mismo cálculo: el informe
para el CFO (Markdown) y la salida estructurada para la IA (JSON).

| Conciliación | CFO | IA |
|---|---|---|
| Flujo canal → banco (`conciliacion reconcile`) | [conciliacion-flujo-wompi-bancolombia.md](conciliacion-flujo-wompi-bancolombia.md) | [conciliacion-flujo-wompi-bancolombia.json](conciliacion-flujo-wompi-bancolombia.json) |
| Ledger Wompi vs libro Odoo (`conciliacion reconcile-erp wompi`) | [conciliacion-erp-wompi.md](conciliacion-erp-wompi.md) | [conciliacion-erp-wompi.json](conciliacion-erp-wompi.json) |
| Ledger Bancolombia vs libro Odoo (`conciliacion reconcile-erp bancolombia`) | [conciliacion-erp-bancolombia.md](conciliacion-erp-bancolombia.md) | [conciliacion-erp-bancolombia.json](conciliacion-erp-bancolombia.json) |

Cómo interpretar los estados y los números: sección **«Cómo leer la salida»**
del [README](../../README.md) principal.

Si un cambio en la ingesta o en los motores mueve estos resultados, regenerá
las salidas y actualizá esta copia en el mismo cambio. No depende de la
memoria de nadie: el job `smoke` del CI regenera la conciliación completa sin
red (replay de `data/raw/`, ver ADR-0013) y falla si lo generado no coincide
con esta copia.
