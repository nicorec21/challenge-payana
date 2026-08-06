"""Proyección de la conciliación de flujo al contrato.

Proyección pura: no decide ni calcula nada que el motor no haya concluido ya.
"""

from __future__ import annotations

import hashlib

from ..reconcile.flow.findings import FlowFinding, FlowReport
from .contract import (
    CONTRACT_VERSION,
    ExplanationView,
    FlowFindingView,
    FlowReportView,
    _money,
    now_iso,
)


def finding_id(finding: FlowFinding) -> str:
    """Identificador estable de un finding, entre corridas.

    Deriva de los movimientos que relaciona y de la regla, no de la posición en
    la lista ni de un contador: dos corridas sobre la misma data producen los
    mismos ids, así que un finding se puede citar, comentar y volver a
    encontrar. Mismo criterio que `Movement.id` (ADR-0001).
    """
    material = "\x1f".join([
        finding.explanation.rule_id,
        finding.settlement_movement_id or "",
        finding.bank_movement_id or "",
    ])
    return f"fnd_{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def build_flow_finding(finding: FlowFinding) -> FlowFindingView:
    return FlowFindingView(
        id=finding_id(finding),
        status=finding.status.value,
        is_problem=finding.status.is_problem,
        occurred_on=finding.occurred_on.isoformat() if finding.occurred_on else None,
        settlement_movement_id=finding.settlement_movement_id,
        bank_movement_id=finding.bank_movement_id,
        transaction_ids=list(finding.transaction_ids),
        settlement_amount=_money(finding.settlement_amount) if finding.settlement_amount else None,
        bank_amount=_money(finding.bank_amount) if finding.bank_amount else None,
        difference=_money(d) if (d := finding.difference) else None,
        explanation=ExplanationView.of(finding.explanation),
    )


def build_flow_report(report: FlowReport) -> FlowReportView:
    from ..domain.money import Money

    cov = report.coverage
    en_disputa = Money.sum(
        f.explanation.unexplained for f in report.problems if f.explanation.unexplained
    )
    return FlowReportView(
        contract_version=CONTRACT_VERSION,
        generated_at=now_iso(),
        channel_ledger_id=report.channel_ledger_id,
        bank_ledger_id=report.bank_ledger_id,
        coverage={
            "channel": _range(cov.channel),
            "bank": _range(cov.bank),
            "overlap": _range(cov.overlap),
        },
        counts=report.counts(),
        by_confidence=report.by_confidence(),
        matched_amount=_money(report.matched_amount()),
        unexplained_total=_money(report.unexplained_total()),
        disputed_amount=_money(en_disputa),
        rounding_amount=_money(report.unexplained_total() - en_disputa),
        problem_count=len(report.problems),
        findings=[build_flow_finding(f) for f in report.findings],
    )


def _range(r: tuple | None) -> dict[str, str] | None:
    return {"from": r[0].isoformat(), "to": r[1].isoformat()} if r else None
