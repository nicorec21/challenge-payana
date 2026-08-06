"""Repositorio SQLite. Un archivo, sin ORM, sin servidor.

La idempotencia la garantiza el motor vía `UNIQUE(source_id, external_id)`, no
el código de ingesta: `INSERT ... ON CONFLICT DO NOTHING` no puede olvidarse de
chequear. Ver ADR-0003.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..domain.ledger import Account, Ledger
from ..domain.money import Money
from ..domain.movement import Movement, MovementKind, MovementStatus

_SCHEMA = Path(__file__).with_name("schema.sql")


class SqliteRepository:
    """Persistencia de cuentas y movimientos.

    Puerto delgado a propósito: si el volumen justificara Postgres, se cambia
    esta clase sin tocar el dominio.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Todo o nada. Una ingesta interrumpida no deja el ledger a medias."""
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # -- cuentas ----------------------------------------------------------

    def save_account(self, account: Account) -> None:
        self._conn.execute(
            "INSERT INTO account (id, name, currency, role) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "currency=excluded.currency, role=excluded.role",
            (account.id, account.name, account.currency, account.role),
        )

    def get_account(self, account_id: str) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM account WHERE id = ?", (account_id,)
        ).fetchone()
        return _to_account(row) if row else None

    def accounts(self) -> list[Account]:
        rows = self._conn.execute("SELECT * FROM account ORDER BY id").fetchall()
        return [_to_account(r) for r in rows]

    # -- movimientos ------------------------------------------------------

    def save_movements(self, movements: Iterable[Movement]) -> int:
        """Guarda movimientos. Devuelve cuántos eran nuevos.

        Los duplicados los descarta el `UNIQUE(source_id, external_id)`; no hay
        un SELECT previo que pueda quedar desincronizado bajo concurrencia.
        """
        rows = [_to_row(m) for m in movements]
        if not rows:
            return 0
        with self.transaction() as conn:
            before = conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
            conn.executemany(
                """
                INSERT INTO movement (
                    id, ledger_id, source_id, external_id, occurred_on, occurred_at,
                    amount, currency, kind, status, description, reference,
                    counterparty, metadata, raw_ref
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id, external_id) DO NOTHING
                """,
                rows,
            )
            after = conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
        return after - before

    def save_ledger(self, ledger: Ledger) -> int:
        self.save_account(ledger.account)
        return self.save_movements(ledger.movements)

    def load_ledger(
        self,
        ledger_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> Ledger:
        account = self.get_account(ledger_id)
        if account is None:
            raise KeyError(f"Cuenta desconocida: {ledger_id}")
        ledger = Ledger(account=account)
        ledger.extend(self.movements(ledger_id, start=start, end=end))
        return ledger

    def movements(
        self,
        ledger_id: str | None = None,
        *,
        start: date | None = None,
        end: date | None = None,
        kinds: Sequence[MovementKind] | None = None,
        only_approved: bool = False,
    ) -> list[Movement]:
        clauses, params = [], []
        if ledger_id is not None:
            clauses.append("ledger_id = ?")
            params.append(ledger_id)
        if start is not None:
            clauses.append("occurred_on >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("occurred_on <= ?")
            params.append(end.isoformat())
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(k.value for k in kinds)
        if only_approved:
            clauses.append("status = ?")
            params.append(MovementStatus.APPROVED.value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM movement{where} ORDER BY occurred_on, id", params
        ).fetchall()
        return [_to_movement(r) for r in rows]

    def count(self, ledger_id: str | None = None) -> int:
        if ledger_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM movement").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM movement WHERE ledger_id = ?", (ledger_id,)
        ).fetchone()[0]

    # -- corridas de conciliación -----------------------------------------

    def save_flow_run(self, view: Any, *, run_id: str | None = None) -> str:
        """Persiste una corrida de conciliación con todos sus findings.

        Se guardan varias corridas a propósito: comparar el resultado antes y
        después de cambiar una regla es la única forma de saber si el cambio
        mejoró algo. El `id` del run es determinista sobre el período y los
        ledgers, así que volver a correr sobre la misma data pisa la corrida
        anterior en vez de acumular duplicados.

        La `Explanation` va como JSON en una columna y los campos que se
        consultan salen a columnas indexadas: su forma depende de la regla y va
        a cambiar mientras se afinan (ADR-0003).
        """
        import json as _json

        run_id = run_id or _flow_run_id(view)
        with self.transaction() as conn:
            conn.execute("DELETE FROM reconciliation_run WHERE id = ?", (run_id,))
            conn.execute(
                "INSERT INTO reconciliation_run (id, kind, started_at, finished_at, params, stats) "
                "VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    "flow",
                    view.generated_at,
                    view.generated_at,
                    _json.dumps(
                        {
                            "channel": view.channel_ledger_id,
                            "bank": view.bank_ledger_id,
                            "coverage": view.coverage,
                            "contract_version": view.contract_version,
                        },
                        ensure_ascii=False,
                    ),
                    _json.dumps(
                        {
                            "counts": view.counts,
                            "by_confidence": view.by_confidence,
                            "problem_count": view.problem_count,
                            "matched_amount_cents": view.matched_amount["cents"],
                            "unexplained_cents": view.unexplained_total["cents"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

            from ..report.contract import to_dict

            for f in view.findings:
                e = f.explanation
                conn.execute(
                    "INSERT INTO finding (id, run_id, status, rule_id, confidence, "
                    "gross, net, unexplained, currency, explanation) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        f.id, run_id, f.status, e.rule_id, e.confidence,
                        e.gross["cents"] if e.gross else None,
                        e.net["cents"] if e.net else None,
                        e.unexplained["cents"] if e.unexplained else None,
                        (e.gross or e.net or {}).get("currency", "COP"),
                        _json.dumps(to_dict(e), ensure_ascii=False),
                    ),
                )
                filas = [(f.id, mid, "source") for mid in e.source_movement_ids]
                filas += [(f.id, mid, "target") for mid in e.target_movement_ids]
                conn.executemany(
                    "INSERT OR IGNORE INTO finding_movement (finding_id, movement_id, side) "
                    "VALUES (?,?,?)",
                    filas,
                )
        return run_id

    def latest_flow_run(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM reconciliation_run WHERE kind = 'flow' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def findings_of(self, run_id: str, status: str | None = None) -> list[dict[str, Any]]:
        import json as _json

        sql = "SELECT * FROM finding WHERE run_id = ?"
        params: list[Any] = [run_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = self._conn.execute(sql + " ORDER BY id", params).fetchall()
        return [{**dict(r), "explanation": _json.loads(r["explanation"])} for r in rows]

    def findings_touching(self, movement_id: str) -> list[dict[str, Any]]:
        """Qué conclusiones involucran a un movimiento.

        Es `Trazar(Movimiento)` de las primitivas sugeridas: dado un pago,
        devuelve dónde terminaron sus fondos.
        """
        import json as _json

        rows = self._conn.execute(
            "SELECT f.*, fm.side FROM finding f "
            "JOIN finding_movement fm ON fm.finding_id = f.id "
            "WHERE fm.movement_id = ? ORDER BY f.id",
            (movement_id,),
        ).fetchall()
        return [{**dict(r), "explanation": _json.loads(r["explanation"])} for r in rows]

    def date_range(self, ledger_id: str) -> tuple[date, date] | None:
        row = self._conn.execute(
            "SELECT MIN(occurred_on), MAX(occurred_on) FROM movement WHERE ledger_id = ?",
            (ledger_id,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return (date.fromisoformat(row[0]), date.fromisoformat(row[1]))


# ── mapeo fila ↔ dataclass ──────────────────────────────────────────────────


def _to_row(m: Movement) -> tuple:
    return (
        m.id,
        m.ledger_id,
        m.source_id,
        m.external_id,
        m.occurred_on.isoformat(),
        m.occurred_at.isoformat() if m.occurred_at else None,
        m.amount.amount,
        m.amount.currency,
        m.kind.value,
        m.status.value,
        m.description,
        m.reference,
        m.counterparty,
        json.dumps(dict(m.metadata), ensure_ascii=False, sort_keys=True, default=str),
        m.raw_ref,
    )


def _to_movement(row: sqlite3.Row) -> Movement:
    return Movement(
        ledger_id=row["ledger_id"],
        source_id=row["source_id"],
        external_id=row["external_id"],
        occurred_on=date.fromisoformat(row["occurred_on"]),
        occurred_at=datetime.fromisoformat(row["occurred_at"]) if row["occurred_at"] else None,
        amount=Money(row["amount"], row["currency"]),
        kind=MovementKind(row["kind"]),
        status=MovementStatus(row["status"]),
        description=row["description"] or "",
        reference=row["reference"],
        counterparty=row["counterparty"],
        metadata=json.loads(row["metadata"]),
        raw_ref=row["raw_ref"],
    )


def _to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"], name=row["name"], currency=row["currency"], role=row["role"]
    )


def _flow_run_id(view: Any) -> str:
    """Id determinista de la corrida: mismos ledgers y mismo período = mismo id.

    Así una re-corrida pisa el resultado anterior en vez de acumular filas, y
    un finding se puede citar entre corridas."""
    import hashlib

    cov = view.coverage.get("overlap") or {}
    material = "".join([
        "flow", view.channel_ledger_id, view.bank_ledger_id,
        str(cov.get("from")), str(cov.get("to")),
    ])
    return f"run_{hashlib.sha256(material.encode()).hexdigest()[:16]}"
