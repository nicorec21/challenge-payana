"""Las herramientas del agente.

Lo que se fija acá no es «que devuelvan algo» sino las tres propiedades que las
hacen útiles y sin las cuales conviene borrarlas:

1. **Resumen, no volcado.** `pendientes()` agrupa: con 415 hallazgos iguales
   devuelve una conclusión, no 415 ítems.
2. **No calculan.** Los números salen del mismo motor que la web y el CLI.
3. **Punteros que resuelven.** El `movement_id` que devuelve una herramienta
   tiene que servir para llamar a la siguiente.
"""

from __future__ import annotations

import json

import pytest

from conciliacion.agent import tools
from conciliacion.domain.ledger import Account, Ledger
from conciliacion.domain.money import Money
from conciliacion.domain.movement import Movement, MovementKind, MovementStatus
from conciliacion.storage.sqlite_repo import SqliteRepository


def mov(ledger, ext, fecha, monto, kind=MovementKind.PAYMENT, **kw):
    from datetime import date

    return Movement(
        ledger_id=ledger,
        source_id=kw.pop("source", "api"),
        external_id=ext,
        occurred_on=date.fromisoformat(fecha),
        amount=Money.parse(monto),
        kind=kind,
        status=kw.pop("status", MovementStatus.APPROVED),
        **kw,
    )


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Una base chica pero con las dos puntas, para que el motor tenga qué hacer."""
    db = tmp_path / "t.db"
    canal = Ledger(Account("wompi", "Wompi", role="channel"))
    canal.extend([
        mov("wompi", "T1", "2026-04-14", "100000", raw_ref="data/raw/x.json#1"),
        mov(
            "wompi", "D1", "2026-04-14", "-97000", MovementKind.SETTLEMENT,
            metadata={"disbursement_id": "D1"},
        ),
    ])
    banco = Ledger(Account("bancolombia", "Banco", role="bank"))
    banco.extend([
        mov(
            "bancolombia", "B1", "2026-04-14", "97000", MovementKind.BANK_CREDIT,
            source="bancolombia_pdf", description="PAGO DE TERC WOMPI S.A.S.",
            raw_ref="data/raw/bancolombia/Extracto_Abril.pdf#pagina=2,y=680",
        ),
    ])
    with SqliteRepository(db) as repo:
        repo.save_ledger(canal)
        repo.save_ledger(banco)

    monkeypatch.setattr(
        tools, "_repo", lambda: SqliteRepository(db)
    )
    return db


class TestEstado:
    def test_responde_las_dos_preguntas(self, base):
        d = tools.estado()
        assert "flujo" in d and "libro_contable" in d
        assert d["contract_version"]

    def test_separa_disputa_de_redondeo(self, base):
        """Mezclarlos hace que el veredicto no cierre con el detalle."""
        f = tools.estado()["flujo"]
        assert "en_disputa" in f and "redondeo_de_estimacion" in f

    def test_fuera_de_cobertura_no_es_un_problema(self, base):
        """«Falta data» y «falta plata» se ven igual y significan lo contrario."""
        f = tools.estado()["flujo"]
        assert "fuera_de_cobertura" in f
        assert f["fuera_de_cobertura"] not in (f["problemas"],) or f["problemas"] == 0

    def test_los_montos_van_en_centavos_enteros(self, base):
        """Un consumidor que sume floats reintroduce el error que el sistema evita."""
        monto = tools.estado()["flujo"]["monto_conciliado"]
        assert isinstance(monto["cents"], int)
        assert "formatted" in monto


class TestPendientes:
    def test_solo_lo_accionable(self, base):
        d = tools.pendientes()
        assert all(p["que"] != "out_of_coverage" for p in d["pendientes"])

    def test_agrupa_en_vez_de_enumerar(self, base):
        """Es la razón de existir de la herramienta: 415 hallazgos → 1 conclusión."""
        d = tools.pendientes()
        grupos = [p for p in d["pendientes"] if p.get("casos", 0) > 1]
        for g in grupos:
            assert g["casos"] > len(g.get("otros_ejemplos", [])) or g["casos"] <= 3

    def test_el_motivo_del_grupo_no_es_el_de_un_caso(self, base):
        """Con 204 hallazgos, el resumen del primero describe un movimiento.

        Pasarlo por el motivo del grupo hace que el agente le diga al usuario
        que el problema es un monto puntual.
        """
        for p in tools.pendientes()["pendientes"]:
            if p.get("casos", 0) > 1:
                assert p["por_que"] != p["ejemplo"]["resumen"]

    def test_cabe_en_un_contexto(self, base):
        """Sin esto la herramienta no aporta nada sobre pedirle todo a la API."""
        d = tools.pendientes()
        assert len(json.dumps(d, ensure_ascii=False)) < 40_000

    def test_respeta_el_limite(self, base):
        d = tools.pendientes(limite=1)
        assert len(d["pendientes"]) <= 1
        assert d["total"] >= d["mostrados"]


class TestPunterosQueResuelven:
    def test_el_id_de_pendientes_sirve_para_explicar(self, base):
        """Un puntero que no resuelve deja al agente en un callejón."""
        for p in tools.pendientes()["pendientes"]:
            ids = p.get("movimientos") or [p["ejemplo"]["movement_id"]]
            for i in [x for x in ids if x]:
                assert "error" not in tools.explicar(i)

    def test_buscar_por_monto_encuentra_el_movimiento(self, base):
        d = tools.buscar(monto="100000")
        assert d["total"] >= 1
        assert any(m["monto"]["cents"] == 10_000_000 for m in d["movimientos"])

    def test_busca_por_valor_absoluto(self, base):
        """El signo depende del lado del asiento; quien copia un número de un
        mail no sabe eso ni tiene por qué."""
        assert tools.buscar(monto="97000")["total"] == 2  # el giro y el crédito

    def test_buscar_por_texto(self, base):
        assert tools.buscar(texto="wompi s.a.s")["total"] == 1

    def test_evidencia_apunta_al_archivo(self, base):
        mid = tools.buscar(texto="wompi s.a.s")["movimientos"][0]["id"]
        e = tools.evidencia(mid)
        assert e["raw_ref"].startswith("data/raw/")
        assert "pagina=2" in e["raw_ref"]


class TestErroresUtiles:
    def test_un_id_inexistente_dice_como_seguir(self, base):
        d = tools.explicar("mov_noexiste")
        assert "error" in d and "buscar" in d["sugerencia"]

    def test_evidencia_de_un_id_inexistente_no_revienta(self, base):
        assert "error" in tools.evidencia("mov_noexiste")


class TestNoEsUnTercerCaminoDeCalculo:
    def test_los_numeros_salen_del_mismo_motor(self, base):
        """Si `tools` calculara por su cuenta, la web y el agente podrían
        afirmar cosas distintas sobre el mismo hecho (ADR-0004)."""
        from conciliacion.reconcile import run

        with SqliteRepository(base) as repo:
            motor = run.flow(repo)

        f = tools.estado()["flujo"]
        assert f["conciliados"] == motor.counts().get("matched", 0)
        assert f["monto_conciliado"]["cents"] == motor.matched_amount().amount
        assert f["problemas"] == len(motor.problems)
