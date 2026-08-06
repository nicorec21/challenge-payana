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
│   └── erp/            Fase 3. Ledger → libro de Odoo
├── storage/        SQLite, un archivo, sin ORM
├── report/         Un contrato, dos proyecciones: Markdown (CFO) y JSON (IA)
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

## Decisiones de diseño

| ADR | Decisión |
|---|---|
| [0001](docs/adr/0001-modelo-canonico.md) | Movement inmutable, ID determinista, `Money` entero |
| [0002](docs/adr/0002-connector-vs-adapter.md) | Connector ⟂ Adapter: N+M clases, no N×M |
| [0003](docs/adr/0003-sqlite-como-persistencia.md) | SQLite stdlib sin ORM; crudos en archivos |
| [0004](docs/adr/0004-explanation-como-objeto-de-dominio.md) | La explicación es estructura, no texto |

## Cómo leer la salida

_(Pendiente: se completa con la salida real sobre los datos provistos.)_

## Agregar una fuente nueva

_(Pendiente: se documenta con el ejemplo del POS, mostrando el costo en líneas.)_
