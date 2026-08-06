-- Esquema SQLite. Un archivo, sin servidor, sin ORM.
--
-- Decisión clave: la clave de idempotencia (source_id, external_id) es un
-- UNIQUE INDEX real. El motor la garantiza; no depende de que el código de
-- ingesta se acuerde de chequear. Ver ADR-0003.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS account (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    currency  TEXT NOT NULL DEFAULT 'COP',
    role      TEXT NOT NULL DEFAULT 'channel'   -- 'channel' | 'bank'
);

CREATE TABLE IF NOT EXISTS movement (
    id           TEXT PRIMARY KEY,              -- hash determinista, ver Movement.id
    ledger_id    TEXT NOT NULL REFERENCES account(id),
    source_id    TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    occurred_on  TEXT NOT NULL,                 -- ISO-8601 date
    occurred_at  TEXT,                          -- ISO-8601 datetime, si la fuente lo da
    amount       INTEGER NOT NULL,              -- unidades menores, con signo
    currency     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    reference    TEXT,
    counterparty TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',    -- JSON
    raw_ref      TEXT,

    UNIQUE (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_movement_ledger_date ON movement (ledger_id, occurred_on);
CREATE INDEX IF NOT EXISTS idx_movement_kind ON movement (ledger_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_movement_amount ON movement (ledger_id, amount);

-- Una corrida de conciliación. Guardamos varias para poder comparar antes y
-- después de agregar una fuente o de cambiar una regla.
CREATE TABLE IF NOT EXISTS reconciliation_run (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,                  -- 'flow' | 'erp'
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    params      TEXT NOT NULL DEFAULT '{}',     -- JSON: ventanas, tolerancias, versión de reglas
    stats       TEXT NOT NULL DEFAULT '{}'      -- JSON: conteos por estado
);

-- Una conclusión del motor. La explicación va serializada como JSON porque su
-- forma depende de la regla; los campos que se consultan salen a columnas.
CREATE TABLE IF NOT EXISTS finding (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES reconciliation_run(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,                -- matched | unmatched_source | unmatched_target | ...
    rule_id       TEXT NOT NULL,
    confidence    TEXT NOT NULL,
    gross         INTEGER,
    net           INTEGER,
    unexplained   INTEGER,
    currency      TEXT NOT NULL DEFAULT 'COP',
    explanation   TEXT NOT NULL                 -- JSON: Explanation completa
);

CREATE INDEX IF NOT EXISTS idx_finding_run_status ON finding (run_id, status);
CREATE INDEX IF NOT EXISTS idx_finding_rule ON finding (run_id, rule_id);

-- Qué movimientos participan de qué finding, y de qué lado. Tabla puente para
-- poder responder Trazar(Movimiento) con un solo JOIN.
CREATE TABLE IF NOT EXISTS finding_movement (
    finding_id  TEXT NOT NULL REFERENCES finding(id) ON DELETE CASCADE,
    movement_id TEXT NOT NULL,
    side        TEXT NOT NULL,                  -- 'source' | 'target'
    PRIMARY KEY (finding_id, movement_id, side)
);

CREATE INDEX IF NOT EXISTS idx_finding_movement_mov ON finding_movement (movement_id);
