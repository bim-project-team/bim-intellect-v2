"""Conservative LEED-oriented evidence assessment states.

This module does not infer certification or implement credit rules. Positive or
negative outcomes require an explicitly registered deterministic evaluator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

ASSESSMENT_STATUSES = frozenset({
    "satisfied_from_available_evidence",
    "not_satisfied_from_available_evidence",
    "insufficient_evidence",
    "not_automatically_evaluable",
})


@dataclass(frozen=True)
class LeedAssessment:
    status: str
    has_bim_sustainability_evidence: bool
    has_document_evidence: bool
    evaluator_id: str | None = None
    explanation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def assess_available_evidence(
    *,
    has_bim_sustainability_evidence: bool,
    has_document_evidence: bool,
    evaluator_id: str | None = None,
    deterministic_outcome: bool | None = None,
) -> LeedAssessment:
    if not has_bim_sustainability_evidence or not has_document_evidence:
        missing = []
        if not has_bim_sustainability_evidence:
            missing.append("BIM sustainability results")
        if not has_document_evidence:
            missing.append("retrieved LEED/sustainability document evidence")
        return LeedAssessment(
            "insufficient_evidence",
            has_bim_sustainability_evidence,
            has_document_evidence,
            evaluator_id,
            "Missing " + " and ".join(missing) + ".",
        )
    if evaluator_id is None or deterministic_outcome is None:
        return LeedAssessment(
            "not_automatically_evaluable", True, True, evaluator_id,
            "Both evidence sources are available, but no reviewed deterministic evaluator is registered for this requirement.",
        )
    return LeedAssessment(
        "satisfied_from_available_evidence" if deterministic_outcome else "not_satisfied_from_available_evidence",
        True,
        True,
        evaluator_id,
        "The status reflects only the available evidence and the named deterministic evaluator; it is not certification.",
    )
