"""Repositorio SQLite. Un archivo, sin ORM, sin servidor.

La idempotencia la garantiza el motor vía `UNIQUE(source_id, external_id)`, no
el código de ingesta: `INSERT ... ON CONFLICT DO NOTHING` no puede olvidarse de
chequear. Ver ADR-0003.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

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
