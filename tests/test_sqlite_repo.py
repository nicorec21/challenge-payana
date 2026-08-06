"""Repositorio SQLite: roundtrip fiel e idempotencia garantizada por el motor."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from conciliacion.config import BANCOLOMBIA, WOMPI
from conciliacion.domain import Account, Ledger, Money, Movement, MovementKind, MovementStatus
from conciliacion.ingest.adapters.bancolombia_pdf import BancolombiaPdfAdapter
from conciliacion.ingest.connectors.local_file import LocalFileConnector
from conciliacion.ingest.registry import SourceRegistry, SourceSpec, ingest_all
from conciliacion.storage.sqlite_repo import SqliteRepository

FIXTURES = Path(__file__).parent / "fixtures" / "bancolombia"


@pytest.fixture
def repo(tmp_path):
    with SqliteRepository(tmp_path / "test.db") as r:
        yield r


def mov(external_id="TX-1", **kw) -> Movement:
    base = dict(
        ledger_id="wompi",
        source_id="wompi_api_transactions",
        external_id=external_id,
        occurred_on=date(2026, 4, 14),
        amount=Money(22_917_500),
        kind=MovementKind.PAYMENT,
    )
    return Movement(**{**base, **kw})


class TestCuentas:
    def test_roundtrip(self, repo):
        repo.save_account(WOMPI)
        assert repo.get_account("wompi") == WOMPI

    def test_upsert_actualiza(self, repo):
        repo.save_account(WOMPI)
        repo.save_account(Account("wompi", "Nombre nuevo", "COP", "channel"))
        assert repo.get_account("wompi").name == "Nombre nuevo"
        assert len(repo.accounts()) == 1

    def test_cuenta_inexistente(self, repo):
        assert repo.get_account("nope") is None


class TestRoundtripDeMovimientos:
    def test_preserva_todos_los_campos(self, repo):
        repo.save_account(WOMPI)
        original = mov(
            occurred_at=datetime(2026, 4, 14, 13, 44, tzinfo=timezone.utc),
            status=MovementStatus.DECLINED,
            description="Pago Wompi CARD",
            reference="8s9n47ejuyuw8gqeh38zb",
            counterparty=None,
            metadata={"disbursement_id": 3136141, "payment_method_type": "CARD"},
            raw_ref="data/raw/wompi/api/transactions/p001.json#id=TX-1",
        )
        repo.save_movements([original])
        (recuperado,) = repo.movements("wompi")

        assert recuperado == original
        assert recuperado.id == original.id

    def test_money_sobrevive_sin_perder_centavos(self, repo):
        """Se guarda como entero en unidades menores; nada de floats en el medio."""
        repo.save_account(WOMPI)
        repo.save_movements([mov(amount=Money.parse("840763.07"))])
        (m,) = repo.movements("wompi")
        assert m.amount == Money.parse("840763.07")
        assert m.amount.amount == 84_076_307

    def test_montos_negativos(self, repo):
        repo.save_account(WOMPI)
        repo.save_movements([mov(amount=Money.parse("-804163.63"), kind=MovementKind.SETTLEMENT)])
        (m,) = repo.movements("wompi")
        assert m.amount == Money.parse("-804163.63")

    def test_metadata_con_tipos_mixtos(self, repo):
        repo.save_account(WOMPI)
        repo.save_movements([mov(metadata={"n": 3136141, "s": "x", "b": True, "z": None})])
        (m,) = repo.movements("wompi")
        assert m.metadata == {"n": 3136141, "s": "x", "b": True, "z": None}


class TestIdempotencia:
    def test_el_motor_rechaza_el_duplicado(self, repo):
        """`UNIQUE(source_id, external_id)` en el esquema: no depende de que el
        código de ingesta se acuerde de chequear."""
        repo.save_account(WOMPI)
        assert repo.save_movements([mov()]) == 1
        assert repo.save_movements([mov()]) == 0
        assert repo.count("wompi") == 1

    def test_misma_external_id_de_fuentes_distintas_coexiste(self, repo):
        repo.save_account(WOMPI)
        repo.save_movements([mov(source_id="wompi_api_transactions")])
        repo.save_movements([mov(source_id="wompi_disbursement_csv")])
        assert repo.count("wompi") == 2

    def test_lote_mixto_cuenta_solo_los_nuevos(self, repo):
        repo.save_account(WOMPI)
        repo.save_movements([mov("A")])
        assert repo.save_movements([mov("A"), mov("B"), mov("C")]) == 2

    def test_la_transaccion_revierte(self, repo):
        """Una ingesta interrumpida no deja el ledger a medias."""
        repo.save_account(WOMPI)
        repo.save_movements([mov("A")])
        with pytest.raises(RuntimeError):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO movement (id, ledger_id, source_id, external_id, "
                    "occurred_on, amount, currency, kind, status) "
                    "VALUES ('x','wompi','s','B','2026-01-01',1,'COP','payment','approved')"
                )
                raise RuntimeError("boom")
        assert repo.count("wompi") == 1


class TestConsultas:
    @pytest.fixture
    def poblado(self, repo):
        repo.save_account(WOMPI)
        repo.save_movements([
            mov("A", occurred_on=date(2026, 4, 13)),
            mov("B", occurred_on=date(2026, 4, 14), kind=MovementKind.FEE, amount=Money(-1000)),
            mov("C", occurred_on=date(2026, 4, 20), status=MovementStatus.DECLINED),
            mov("D", occurred_on=date(2026, 5, 1), kind=MovementKind.SETTLEMENT, amount=Money(-500)),
        ])
        return repo

    def test_filtra_por_rango(self, poblado):
        movs = poblado.movements("wompi", start=date(2026, 4, 14), end=date(2026, 4, 20))
        assert {m.external_id for m in movs} == {"B", "C"}

    def test_filtra_por_kind(self, poblado):
        movs = poblado.movements("wompi", kinds=[MovementKind.FEE, MovementKind.SETTLEMENT])
        assert {m.external_id for m in movs} == {"B", "D"}

    def test_solo_aprobados(self, poblado):
        movs = poblado.movements("wompi", only_approved=True)
        assert "C" not in {m.external_id for m in movs}

    def test_orden_determinista(self, poblado):
        a = [m.id for m in poblado.movements("wompi")]
        b = [m.id for m in poblado.movements("wompi")]
        assert a == b

    def test_date_range(self, poblado):
        assert poblado.date_range("wompi") == (date(2026, 4, 13), date(2026, 5, 1))

    def test_date_range_vacio(self, repo):
        assert repo.date_range("wompi") is None


class TestLedgerCompleto:
    def test_persistir_y_recuperar_un_ledger_real(self, tmp_path):
        """De extractos reales a SQLite y de vuelta, sin perder nada."""
        registry = SourceRegistry()
        registry.register_account(BANCOLOMBIA)
        registry.register_source(
            SourceSpec(
                name="bancolombia",
                connector=LocalFileConnector(FIXTURES, "*.pdf"),
                adapters=(BancolombiaPdfAdapter(),),
            )
        )
        original, _ = ingest_all(registry, "bancolombia", strict=True)

        with SqliteRepository(tmp_path / "real.db") as repo:
            assert repo.save_ledger(original) == 214
            recuperado = repo.load_ledger("bancolombia")

        assert len(recuperado) == len(original) == 214
        assert [m.id for m in recuperado] == [m.id for m in original]
        assert recuperado.balance() == original.balance()
        assert recuperado.date_range == original.date_range

    def test_la_cadena_de_saldos_sobrevive_al_roundtrip(self, tmp_path):
        """El invariante del extracto se sigue verificando después de pasar
        por la base.

        Hay que reordenar por `metadata["orden"]`: el ledger ordena por
        (fecha, id) —determinista, ver ADR-0001— y ese no es el orden del
        documento. La cadena de saldos solo existe en orden de documento, así
        que preservarlo es lo que hace el extracto reconstruible.
        """
        registry = SourceRegistry()
        registry.register_account(BANCOLOMBIA)
        registry.register_source(
            SourceSpec("b", LocalFileConnector(FIXTURES, "Extracto_Abril.pdf"),
                       (BancolombiaPdfAdapter(),))
        )
        original, _ = ingest_all(registry, "bancolombia", strict=True)

        with SqliteRepository(tmp_path / "r.db") as repo:
            repo.save_ledger(original)
            movs = repo.movements("bancolombia")

        en_orden = sorted(movs, key=lambda m: m.metadata["orden"])
        saldos = [Money.parse(m.metadata["saldo"]) for m in en_orden]
        for anterior, actual, m in zip(saldos, saldos[1:], en_orden[1:]):
            assert anterior + m.amount == actual

    def test_el_orden_del_documento_es_distinto_del_orden_del_ledger(self, tmp_path):
        """Documenta por qué hace falta `orden`: dentro de un mismo día, el
        ledger ordena por id (hash) y eso no es el orden del extracto."""
        registry = SourceRegistry()
        registry.register_account(BANCOLOMBIA)
        registry.register_source(
            SourceSpec("b", LocalFileConnector(FIXTURES, "Extracto_Abril.pdf"),
                       (BancolombiaPdfAdapter(),))
        )
        ledger, _ = ingest_all(registry, "bancolombia", strict=True)
        ordenes = [m.metadata["orden"] for m in ledger]
        assert ordenes != sorted(ordenes)
        assert sorted(ordenes) == list(range(len(ledger)))

    def test_reingerir_sobre_base_existente_no_duplica(self, tmp_path):
        registry = SourceRegistry()
        registry.register_account(BANCOLOMBIA)
        registry.register_source(
            SourceSpec("b", LocalFileConnector(FIXTURES, "*.pdf"), (BancolombiaPdfAdapter(),))
        )
        db = tmp_path / "r.db"
        for _ in range(3):
            ledger, _ = ingest_all(registry, "bancolombia", strict=True)
            with SqliteRepository(db) as repo:
                repo.save_ledger(ledger)
        with SqliteRepository(db) as repo:
            assert repo.count("bancolombia") == 214

    def test_load_ledger_de_cuenta_inexistente(self, repo):
        with pytest.raises(KeyError):
            repo.load_ledger("nope")
