"""Salidas de la conciliación de flujo: contrato JSON, reporte CFO, persistencia.

Las dos salidas del enunciado —reporte legible y JSON estructurado— se generan
del mismo `FlowReport`. Estos tests fijan que no puedan divergir.
"""

from __future__ import annotations

from datetime import date

import pytest

from conciliacion.domain import (
    Account,
    Ledger,
    Money,
    Movement,
    MovementKind,
    MovementStatus,
)
from conciliacion.reconcile.calendar import BusinessCalendar
from conciliacion.reconcile.flow.engine import reconcile_flow
from conciliacion.reconcile.flow.findings import Coverage
from conciliacion.report.cfo import render_flow_report
from conciliacion.report.contract import to_dict
from conciliacion.report.flow_views import build_flow_report, finding_id
from conciliacion.storage.sqlite_repo import SqliteRepository

CAL = BusinessCalendar("CO")
COV = Coverage(
    channel=(date(2026, 1, 1), date(2026, 12, 31)),
    bank=(date(2026, 1, 1), date(2026, 12, 31)),
)


def _mov(ledger, source, ext, day, monto, kind, **kw):
    return Movement(
        ledger_id=ledger, source_id=source, external_id=ext, occurred_on=day,
        amount=Money.parse(monto), kind=kind,
        status=kw.pop("status", MovementStatus.APPROVED),
        description=kw.pop("description", ""),
        metadata=kw.pop("metadata", {}),
    )


@pytest.fixture
def report():
    """Escenario con un caso de cada tipo: match declarado, match inferido,
    crédito huérfano y giro fuera de cobertura."""
    canal = Ledger(Account("wompi", "Canal", role="channel"))
    canal.extend([
        # match con desglose declarado -> exact
        _mov("wompi", "api", "T1", date(2026, 4, 13), "100", MovementKind.PAYMENT,
             metadata={"disbursement_id": "D1", "payment_method_type": "CARD"}),
        _mov("wompi", "csv", "T1:comisión", date(2026, 4, 13), "-10", MovementKind.FEE,
             metadata={"transaction_id": "T1", "deduction": "comisión"}),
        _mov("wompi", "dis", "D1", date(2026, 4, 14), "-90", MovementKind.SETTLEMENT,
             metadata={"disbursement_id": "D1"}),
        # match sin desglose -> high
        _mov("wompi", "api", "T2", date(2026, 4, 20), "840763", MovementKind.PAYMENT,
             metadata={"disbursement_id": "D2", "payment_method_type": "CARD"}),
        _mov("wompi", "dis", "D2", date(2026, 4, 21), "-804163.63", MovementKind.SETTLEMENT,
             metadata={"disbursement_id": "D2"}),
    ])
    banco = Ledger(Account("bancolombia", "Banco", role="bank"))
    banco.extend([
        _mov("bancolombia", "pdf", "B1", date(2026, 4, 14), "90", MovementKind.BANK_CREDIT,
             description="PAGO DE TERC WOMPI S.A.S."),
        _mov("bancolombia", "pdf", "B2", date(2026, 4, 21), "804163.63", MovementKind.BANK_CREDIT,
             description="PAGO DE TERC WOMPI S.A.S."),
        _mov("bancolombia", "pdf", "B3", date(2026, 4, 25), "555", MovementKind.BANK_CREDIT,
             description="PAGO DE PROV WOMPI S.A.S."),
        _mov("bancolombia", "pdf", "N1", date(2026, 4, 26), "999", MovementKind.BANK_CREDIT,
             description="ABONO INTERESES AHORROS"),
    ])
    return reconcile_flow(canal, banco, calendar=CAL, coverage=COV)


# ── contrato JSON ───────────────────────────────────────────────────────────


class TestContratoJson:
    def test_forma_de_nivel_1(self, report):
        j = to_dict(build_flow_report(report))
        assert set(j) == {
            "contract_version", "generated_at", "channel_ledger_id", "bank_ledger_id",
            "coverage", "counts", "by_confidence", "matched_amount", "unexplained_total",
            "disputed_amount", "rounding_amount", "problem_count", "findings",
        }

    def test_nunca_hay_floats(self, report):
        """Un consumidor que sume floats reintroduce el error de redondeo que
        todo el sistema evita."""
        def revisar(node):
            if isinstance(node, float):
                raise AssertionError(f"float en el contrato: {node}")
            if isinstance(node, dict):
                for v in node.values():
                    revisar(v)
            if isinstance(node, list):
                for v in node:
                    revisar(v)

        revisar(to_dict(build_flow_report(report)))

    def test_cada_finding_trae_su_explicacion(self, report):
        """Invariante del sistema: no hay conclusión sin explicación."""
        j = to_dict(build_flow_report(report))
        for f in j["findings"]:
            assert f["explanation"]["rule_id"]
            assert f["explanation"]["summary"]
            assert f["explanation"]["confidence"]

    def test_el_origen_de_cada_ajuste_viaja(self, report):
        """`declared` vs `inferred` es lo que distingue lo observado de lo
        supuesto. Sin ese campo, un consumidor los trata igual."""
        j = to_dict(build_flow_report(report))
        fuentes = {
            a["source"]
            for f in j["findings"]
            for a in f["explanation"]["adjustments"]
        }
        assert fuentes == {"declared", "inferred"}

    def test_disputed_no_incluye_el_redondeo(self, report):
        """El total del veredicto tiene que coincidir con la suma del detalle.
        `unexplained_total` incluye los centavos de matches exitosos."""
        j = to_dict(build_flow_report(report))
        assert (
            j["disputed_amount"]["cents"] + j["rounding_amount"]["cents"]
            == j["unexplained_total"]["cents"]
        )

    def test_out_of_coverage_no_cuenta_como_problema(self):
        canal = Ledger(Account("wompi", "C", role="channel"))
        canal.extend([
            _mov("wompi", "dis", "D9", date(2026, 5, 4), "-100", MovementKind.SETTLEMENT,
                 metadata={"disbursement_id": "D9"}),
        ])
        banco = Ledger(Account("bancolombia", "B", role="bank"))
        banco.extend([
            _mov("bancolombia", "pdf", "X", date(2026, 4, 1), "1", MovementKind.BANK_CREDIT),
        ])
        cov = Coverage(channel=(date(2026, 1, 1), date(2026, 12, 31)),
                       bank=(date(2026, 1, 1), date(2026, 4, 30)))
        j = to_dict(build_flow_report(reconcile_flow(canal, banco, calendar=CAL, coverage=cov)))
        assert j["counts"]["out_of_coverage"] == 1
        assert j["problem_count"] == 0
        assert all(not f["is_problem"] for f in j["findings"])


class TestIdentidadDeFindings:
    def test_es_determinista(self, report):
        a = [f.id for f in build_flow_report(report).findings]
        b = [f.id for f in build_flow_report(report).findings]
        assert a == b

    def test_deriva_de_lo_que_relaciona(self, report):
        """No de la posición en la lista: un finding se puede citar y volver a
        encontrar entre corridas."""
        for f in report.findings:
            assert finding_id(f).startswith("fnd_")
        assert len({finding_id(f) for f in report.findings}) == len(report.findings)


# ── reporte del CFO ─────────────────────────────────────────────────────────


class TestReporteCfo:
    def test_empieza_por_la_respuesta(self, report):
        md = render_flow_report(report)
        cuerpo = md.split("## ", 2)
        assert "requieren revisión" in cuerpo[1] or "está explicado" in cuerpo[1]

    def test_separa_faltantes_de_falta_de_datos(self):
        canal = Ledger(Account("wompi", "C", role="channel"))
        canal.extend([
            _mov("wompi", "dis", "D1", date(2026, 4, 14), "-100", MovementKind.SETTLEMENT,
                 metadata={"disbursement_id": "D1"}),
            _mov("wompi", "dis", "D2", date(2026, 5, 4), "-200", MovementKind.SETTLEMENT,
                 metadata={"disbursement_id": "D2"}),
        ])
        banco = Ledger(Account("bancolombia", "B", role="bank"))
        banco.extend([
            _mov("bancolombia", "pdf", "X", date(2026, 4, 1), "1", MovementKind.BANK_CREDIT),
        ])
        cov = Coverage(channel=(date(2026, 1, 1), date(2026, 12, 31)),
                       bank=(date(2026, 1, 1), date(2026, 4, 30)))
        md = render_flow_report(reconcile_flow(canal, banco, calendar=CAL, coverage=cov))

        assert "Fuera del alcance de los datos" in md
        assert "no afirma que falte plata" in md
        # el faltante real va en la sección de problemas, no en la de alcance
        assert md.index("Casos que requieren revisión") < md.index("Fuera del alcance")

    def test_marca_los_ajustes_estimados(self, report):
        """Un ajuste inferido y uno declarado no se pueden ver igual."""
        md = render_flow_report(report)
        assert "*(estimado)*" in md or "estimadas" in md

    def test_explica_el_redondeo_en_vez_de_esconderlo(self, report):
        """El párrafo aparece solo si hay residuo en matches EXITOSOS.

        `unexplained_total` no sirve como condición: incluye lo atribuible a los
        problemas, que ya se detalla aparte. Lo que hay que explicar es el
        residuo de haber estimado comisiones en conciliaciones que sí cerraron."""
        vista = build_flow_report(report)
        md = render_flow_report(report)
        if vista.rounding_amount["cents"]:
            assert "redondeo" in md.lower()
            assert vista.rounding_amount["formatted"] in md
        else:
            assert "redondeo" not in md.lower()

    def test_el_redondeo_se_reporta_cuando_existe(self):
        """Un match con comisiones inferidas cuyo residuo no es cero."""
        canal = Ledger(Account("wompi", "C", role="channel"))
        canal.extend([
            _mov("wompi", "api", "T1", date(2026, 4, 20), "121849", MovementKind.PAYMENT,
                 metadata={"disbursement_id": "D1", "payment_method_type": "CARD"}),
            _mov("wompi", "dis", "D1", date(2026, 4, 21), "-116137.77", MovementKind.SETTLEMENT,
                 metadata={"disbursement_id": "D1"}),
        ])
        banco = Ledger(Account("bancolombia", "B", role="bank"))
        banco.extend([
            _mov("bancolombia", "pdf", "B1", date(2026, 4, 21), "116137.77",
                 MovementKind.BANK_CREDIT, description="PAGO DE TERC WOMPI S.A.S."),
        ])
        r = reconcile_flow(canal, banco, calendar=CAL, coverage=COV)
        vista = build_flow_report(r)
        if vista.rounding_amount["cents"]:
            assert "redondeo" in render_flow_report(r).lower()

    def test_cierra_con_la_nota_metodologica(self, report):
        md = render_flow_report(report)
        assert md.index("Cómo leer esto") > md.index("Conciliado")
        assert "no se adivina" in md.lower()

    def test_es_markdown_valido_y_no_vacio(self, report):
        md = render_flow_report(report)
        assert md.startswith("# Conciliación de flujo")
        assert md.endswith("\n")
        assert len(md.splitlines()) > 20


# ── persistencia ────────────────────────────────────────────────────────────


class TestPersistenciaDeCorridas:
    @pytest.fixture
    def repo(self, tmp_path):
        with SqliteRepository(tmp_path / "run.db") as r:
            yield r

    def test_guarda_y_recupera(self, repo, report):
        view = build_flow_report(report)
        run_id = repo.save_flow_run(view)
        assert run_id.startswith("run_")

        run = repo.latest_flow_run()
        assert run["kind"] == "flow"
        assert repo.findings_of(run_id)

    def test_la_explicacion_sobrevive_al_roundtrip(self, repo, report):
        run_id = repo.save_flow_run(build_flow_report(report))
        guardados = repo.findings_of(run_id)
        for f in guardados:
            assert f["explanation"]["rule_id"] == f["rule_id"]
            assert f["explanation"]["summary"]

    def test_recorrer_de_nuevo_no_acumula_corridas(self, repo, report):
        """El id del run es determinista sobre período y ledgers: una re-corrida
        pisa la anterior en vez de dejar filas huérfanas."""
        view = build_flow_report(report)
        a = repo.save_flow_run(view)
        b = repo.save_flow_run(view)
        assert a == b
        assert len(repo.findings_of(a)) == len(report.findings)

    def test_filtra_por_estado(self, repo, report):
        run_id = repo.save_flow_run(build_flow_report(report))
        matched = repo.findings_of(run_id, status="matched")
        assert matched
        assert all(f["status"] == "matched" for f in matched)

    def test_trazar_un_movimiento(self, repo, report):
        """`Trazar(Movimiento)`: dado un pago, dónde terminaron sus fondos."""
        repo.save_flow_run(build_flow_report(report))
        pago = next(f for f in report.findings if f.transaction_ids)
        encontrados = repo.findings_touching(pago.transaction_ids[0])
        assert encontrados
        assert encontrados[0]["side"] == "source"

    def test_un_movimiento_ajeno_no_aparece(self, repo, report):
        repo.save_flow_run(build_flow_report(report))
        assert repo.findings_touching("mov_inexistente") == []
