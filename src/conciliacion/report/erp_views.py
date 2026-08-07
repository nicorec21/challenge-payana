"""Proyección de la conciliación contra el ERP al contrato."""

from __future__ import annotations

import hashlib

from ..reconcile.erp.findings import ErpFinding, ErpReport, ErpStatus
from .contract import (
    CONTRACT_VERSION,
    ErpFindingView,
    ErpGroupView,
    ErpReportView,
    ExplanationView,
    _money,
    now_iso,
)


def erp_finding_id(finding: ErpFinding) -> str:
    """Id estable entre corridas: deriva de lo que relaciona, no de la posición."""
    material = "\x1f".join([
        finding.explanation.rule_id,
        finding.ledger_movement_id or "",
        finding.book_movement_id or "",
    ])
    return f"erp_{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def build_erp_finding(f: ErpFinding) -> ErpFindingView:
    return ErpFindingView(
        id=erp_finding_id(f),
        status=f.status.value,
        is_problem=f.status.is_problem,
        occurred_on=f.occurred_on.isoformat() if f.occurred_on else None,
        kind=f.kind,
        ledger_movement_id=f.ledger_movement_id,
        book_movement_id=f.book_movement_id,
        erp_line_id=f.erp_line_id,
        erp_move_name=f.erp_move_name,
        ledger_amount=_money(f.ledger_amount) if f.ledger_amount else None,
        book_amount=_money(f.book_amount) if f.book_amount else None,
        difference=_money(d) if (d := f.difference) else None,
        explanation=ExplanationView.of(f.explanation),
    )


def build_erp_report(report: ErpReport) -> ErpReportView:
    cov = report.coverage()
    return ErpReportView(
        contract_version=CONTRACT_VERSION,
        generated_at=now_iso(),
        ledger_id=report.ledger_id,
        book_ledger_id=report.book_ledger_id,
        account_code=report.account_code,
        counts=report.counts(),
        coverage_ratio=round(cov["overall_ratio"], 4),
        coverage={
            **cov,
            "ratio": round(cov["ratio"], 4),
            "overall_ratio": round(cov["overall_ratio"], 4),
            "unrepresentable_total": _money(cov["unrepresentable_total"]),
        },
        matched_amount=_money(report.matched_amount()),
        problem_count=len(report.problems),
        missing_in_erp_by_kind=[
            ErpGroupView(kind=kind, count=info["count"], total=_money(info["total"]))
            for kind, info in report.by_kind(ErpStatus.MISSING_IN_ERP).items()
        ],
        findings=[build_erp_finding(f) for f in report.findings],
    )
