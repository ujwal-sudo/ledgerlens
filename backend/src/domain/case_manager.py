from datetime import datetime
from typing import Optional, Dict, Any, List

from ..db.models import FinancialCase, EvidenceItem


class CaseManager:
    """Manages the case lifecycle state machine.

    Valid transitions (PRD Section 24):
    NEW → TRIAGED
    TRIAGED → INVESTIGATING
    INVESTIGATING → HYPOTHESIS_FORMED | WAITING_FOR_EVIDENCE | ESCALATED
    WAITING_FOR_EVIDENCE → INVESTIGATING | ESCALATED
    HYPOTHESIS_FORMED → PROPOSED
    PROPOSED → AWAITING_APPROVAL | AUTO_RESOLVED
    AWAITING_APPROVAL → RESOLVED | ESCALATED | REJECTED
    RESOLVED → CLOSED
    ESCALATED → INVESTIGATING | RESOLVED | CLOSED
    """

    # Valid state transitions
    VALID_TRANSITIONS = {
        "NEW": ["TRIAGED"],
        "TRIAGED": ["INVESTIGATING"],
        "INVESTIGATING": ["HYPOTHESIS_FORMED", "WAITING_FOR_EVIDENCE", "ESCALATED"],
        "WAITING_FOR_EVIDENCE": ["INVESTIGATING", "ESCALATED"],
        "HYPOTHESIS_FORMED": ["PROPOSED"],
        "PROPOSED": ["AWAITING_APPROVAL", "AUTO_RESOLVED"],
        "AWAITING_APPROVAL": ["RESOLVED", "ESCALATED", "REJECTED"],
        "RESOLVED": ["CLOSED"],
        "ESCALATED": ["INVESTIGATING", "RESOLVED", "CLOSED"],
    }

    def __init__(self, repo):
        self.repo = repo

    def can_transition(self, from_status: str, to_status: str) -> bool:
        """Check if a state transition is valid."""
        allowed = self.VALID_TRANSITIONS.get(from_status, [])
        return to_status in allowed

    def transition_case(self, case_id: str, to_status: str) -> bool:
        """Attempt to transition a case to a new status.

        Returns True if transition succeeded, False if invalid.
        """
        case = self.repo.get_case(case_id)
        if not case:
            return False

        from_status = case.status
        if not self.can_transition(from_status, to_status):
            return False

        case.status = to_status
        # Auto-set resolved_at for terminal states
        if to_status in ("RESOLVED", "ESCALATED", "CLOSED", "REJECTED"):
            case.resolved_at = datetime.utcnow()
        return True

    def new_case(
        self,
        case_id: str,
        object_type: str,
        object_id: str,
        anomaly_type: str | None = None,
    ) -> any:
        """Create a new case with status NEW."""
        return self.repo.create_case(
            case_id=case_id,
            object_type=object_type,
            object_id=object_id,
            anomaly_type=anomaly_type,
        )

    def get_case_status(self, case_id: str) -> str | None:
        """Get the current status of a case."""
        case = self.repo.get_case(case_id)
        if case:
            return case.status
        return None

    def list_cases_by_status(self, status: str) -> list[dict]:
        """List all cases with a given status."""
        cases = self.repo.get_case_status_filter(status)
        return cases

    # --- Evidence management ---

    def add_evidence(
        self,
        case_id: str,
        evidence_id: str,
        claim: str,
        source_type: str,
        source_table: str | None,
        source_id: str | None,
        source_field: str | None,
        value: str,
        agent_step_id: str | None = None,
    ) -> any:
        """Add evidence to a case."""
        return self.repo.create_evidence(
            evidence_id=evidence_id,
            case_id=case_id,
            claim=claim,
            source_type=source_type,
            source_table=source_table,
            source_id=source_id,
            source_field=source_field,
            value=value,
            agent_step_id=agent_step_id,
        )

    def get_case_evidence(self, case_id: str) -> list[dict]:
        """Get all evidence for a case."""
        evidences = self.repo.get_evidence_by_case(case_id)
        return evidences