from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..db.models import Merchant, Payment, Refund, Settlement
from ..db.queries import Repository
from ..domain.calculator import FinancialCalculator
from ..domain.policy import PolicyDecision, PolicyEngine


@dataclass
class AgentStep:
    """A single step in the agent's ReAct trace.

    Every tool call and reasoning step is logged here for audit + UI display.
    """
    step_id: str
    step_type: str  # 'TOOL_CALL' | 'LLM_REASONING' | 'CALCULATION' | 'POLICY_CHECK' | 'CONCLUSION' | 'CASE_UPDATE'
    timestamp: str  # ISO format
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: dict | None = None
    reasoning: str | None = None  # LLM's self-described reasoning
    conclusion: str | None = None  # What this step concluded
    confidence: float | None = None  # Confidence in this step's conclusion
    iteration: int = 0


@dataclass
class AgentState:
    """Mutable state shared across the ReAct loop iterations."""

    case: any  # FinancialCase object
    steps: List[AgentStep] = field(default_factory=list)
    hypotheses: List[any] = field(default_factory=list)  # List[Hypothesis]
    evidence: List[dict] = field(default_factory=list)  # List[Evidence dict]
    iteration: int = 0
    max_iterations: int = 10  # Hard limit — agent cannot run forever
    should_stop: bool = False
    stop_reason: str = ""  # "sufficient_evidence" | "max_iterations" | "insufficient_evidence"


class InvestigationTools:
    """All 6 core tools for the InvestigationAgent ReAct loop.

    Each tool returns structured JSON. All financial calculations within
    tools are deterministic — never LLM.
    """

    def __init__(self, repo, calculator: FinancialCalculator, policy_engine: PolicyEngine):
        self.repo = repo
        self.calculator = calculator
        self.policy_engine = policy_engine

    # --- Tool 1: get_settlement ---

    def get_settlement(self, settlement_id: str) -> dict:
        """Retrieve settlement record with all fields.

        Returns settlement amount, window, cutoff, merchant_id.
        Deterministic DB query — no LLM.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return {"error": f"Settlement {settlement_id} not found"}

        return {
            "settlement_id": settlement.settlement_id,
            "merchant_id": settlement.merchant_id,
            "amount": float(settlement.amount),
            "window_start": settlement.window_start.isoformat() if settlement.window_start else None,
            "cutoff_at": settlement.cutoff_at.isoformat() if settlement.cutoff_at else None,
            "settled_at": settlement.settled_at.isoformat() if settlement.settled_at else None,
            "status": settlement.status,
        }

    # --- Tool 2: get_payments_in_settlement_window ---

    def get_payments_in_settlement_window(self, settlement_id: str) -> dict:
        """Get all captured payments in the settlement's time window for the merchant.

        Deterministic DB query — no LLM.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return {"error": f"Settlement {settlement_id} not found"}

        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        payment_sum = FinancialCalculator.sum_amounts([
            p for p in payments if getattr(p, "status", None) == "captured"
        ])

        return {
            "settlement_id": settlement_id,
            "payment_count": len(payments),
            "total_amount": float(payment_sum),
            "payments": [
                {
                    "payment_id": p.payment_id,
                    "amount": float(p.amount),
                    "captured_at": p.captured_at.isoformat() if p.captured_at else None,
                    "status": p.status,
                }
                for p in payments
            ],
        }

    # --- Tool 3: get_refunds ---

    def get_refunds(self, payment_id: str = None, settlement_id: str = None) -> dict:
        """Get refunds.

        By payment_id for a specific refund chain; by settlement_id for all in window.
        """
        results = {"payment_id": payment_id, "settlement_id": settlement_id, "refunds": []}

        if payment_id:
            # Get refunds for a specific payment
            refunds = self.repo.get_refunds_by_payment(payment_id)
            for r in refunds:
                results["refunds"].append({
                    "refund_id": r.refund_id,
                    "amount": float(r.amount),
                    "status": r.status,
                    "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                })
        elif settlement_id:
            # Get all refunds in the settlement window for the merchant
            s = self.repo.get_settlement(settlement_id)
            if s:
                refunds = self.repo.get_refunds_by_settlement_window(
                    s.merchant_id, s.cutoff_at, status="processed"
                )
                for r in refunds:
                    results["refunds"].append({
                        "refund_id": r.refund_id,
                        "amount": float(r.amount),
                        "status": r.status,
                        "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                    })

        return results

    # --- Tool 4: get_timeline ---

    def get_timeline(self, entity_type: str, entity_id: str) -> dict:
        """Get chronological event timeline for any entity.

        Critical for temporal reasoning — orders events by timestamp.
        """
        # For MVP, we construct a timeline from available records
        # based on the entity type and ID
        from ..db.models import Payment, Refund, Settlement, Fee, Chargeback

        timeline_events = []

        if entity_type == "payment" and entity_id:
            payment = self.repo.get_payment(entity_id)
            if payment and payment.captured_at:
                timeline_events.append({
                    "entity_type": "payment",
                    "entity_id": entity_id,
                    "event": "captured",
                    "timestamp": payment.captured_at.isoformat() if payment.captured_at else None,
                    "amount": float(payment.amount) if payment.amount else 0.0,
                })
            # Also check for refunds linked to this payment
            refunds = self.repo.get_refunds_by_payment(entity_id)
            for r in refunds:
                if r.processed_at:
                    timeline_events.append({
                        "entity_type": "refund",
                        "entity_id": r.refund_id,
                        "event": "processed",
                        "timestamp": r.processed_at.isoformat() if r.processed_at else None,
                        "amount": float(r.amount) if r.amount else 0.0,
                    })

        elif entity_type == "settlement" and entity_id:
            settlement = self.repo.get_settlement(entity_id)
            if settlement:
                timeline_events.append({
                    "entity_type": "settlement",
                    "entity_id": entity_id,
                    "event": "cutoff",
                    "timestamp": settlement.cutoff_at.isoformat() if settlement.cutoff_at else None,
                })
                timeline_events.append({
                    "entity_type": "settlement",
                    "entity_id": entity_id,
                    "event": "settled",
                    "timestamp": settlement.settled_at.isoformat() if settlement.settled_at else None,
                })

                # Add payments in window to timeline
                payments = self.repo.get_captured_payments_in_window(
                    settlement.merchant_id, settlement.window_start, settlement.cutoff_at
                )
                for p in payments[:5]:  # Limit for timeline
                    if p.captured_at:
                        timeline_events.append({
                            "entity_type": "payment",
                            "entity_id": p.payment_id,
                            "event": "captured",
                            "timestamp": p.captured_at.isoformat() if p.captured_at else None,
                            "amount": float(p.amount) if p.amount else 0.0,
                        })

                # Add refunds in window
                refunds = self.repo.get_refunds_by_settlement_window(
                    settlement.merchant_id, settlement.cutoff_at, status="processed"
                )
                for r in refunds[:5]:
                    if r.processed_at:
                        timeline_events.append({
                            "entity_type": "refund",
                            "entity_id": r.refund_id,
                            "event": "processed",
                            "timestamp": r.processed_at.isoformat() if r.processed_at else None,
                            "amount": float(r.amount) if r.amount else 0.0,
                        })

        elif entity_type == "refund" and entity_id:
            refund = self.repo.get_refund(entity_id)
            if refund and refund.processed_at:
                timeline_events.append({
                    "entity_type": "refund",
                    "entity_id": entity_id,
                    "event": "processed",
                    "timestamp": refund.processed_at.isoformat() if refund.processed_at else None,
                    "amount": float(refund.amount) if refund.amount else 0.0,
                })

        # Sort by timestamp
        timeline_events.sort(key=lambda e: e.get("timestamp", "") or "")

        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "timeline": timeline_events,
        }

    # --- Tool 5: calculate_expected_settlement ---

    def calculate_expected_settlement(self, settlement_id: str) -> dict:
        """Calculate expected settlement amount deterministically.

        This is the ReconciliationEngine.reconcile_settlement() call.
        LLM NEVER does this calculation — it's always Decimal arithmetic.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return {"error": f"Settlement {settlement_id} not found"}

        # Get captured payments in window
        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        # Get processed refunds in window
        refunds = self.repo.get_refunds_by_settlement_window(
            settlement.merchant_id, settlement.cutoff_at, status="processed"
        )

        # Get fees for settlement
        fees = self.repo.get_fees_for_settlement(settlement_id)

        # Deterministic calculation using FinancialCalculator
        calculation = FinancialCalculator.calculate_expected_settlement(
            payments, refunds, fees
        )

        expected = Decimal(str(calculation["result"]))
        actual = Decimal(str(settlement.amount))
        variance = FinancialCalculator.calculate_variance(expected, actual)

        return {
            "settlement_id": settlement_id,
            "formula": calculation["formula"],
            "components": calculation["components"],
            "expected_amount": float(expected),
            "actual_amount": float(actual),
            "variance": float(variance),
            "variance_sign": "-" if variance < 0 else "+",
            "deterministic": True,
            "calculation_detail": {
                "gross_payments": calculation["components"]["gross"],
                "total_refunds": calculation["components"]["refunds"],
                "total_fees": calculation["components"]["fees"],
            },
        }

    # --- Tool 5: get_fees_for_settlement ---
    def get_fees_for_settlement(self, settlement_id: str) -> dict:
        """Retrieve all fees associated with a settlement."""
        fees = self.repo.get_fees_for_settlement(settlement_id)
        if isinstance(fees, list):
            total_fees = sum(f.amount for f in fees if f.amount)
            fee_details = [
                {"fee_id": f.fee_id, "amount": float(f.amount), "fee_type": f.fee_type}
                for f in fees
            ]
        else:
            total_fees = Decimal("0.00")
            fee_details = []
        return {
            "total_fees": float(total_fees) if total_fees else 0.0,
            "fee_details": fee_details,
        }

    # --- Tool 6: request_human_review ---

    def request_human_review(self, case_id: str, reason: str) -> dict:
        """Escalate case to human review queue.

        Returns escalation record with case details and escalation reason.
        """
        # Update case status to AWAITING_APPROVAL / ESCALATED
        from ..domain.case_manager import CaseManager
        from ..db.models import FinancialCase

        case = self.repo.get_case(case_id)
        if not case:
            return {"error": f"Case {case_id} not found"}

        # Transition case status
        transitioned = self.repo.transition_case(case_id, "AWAITING_APPROVAL")
        if not transitioned:
            return {"error": f"Could not transition case {case_id}"}

        # Update case with root cause and variance if available
        if case.root_cause:
            pass  # Already set

        # Create evidence record for the escalation
        from ..db.queries import EvidenceItem
        # The evidence is already linked; just return the escalation record

        return {
            "case_id": case_id,
            "escalation_action": "AWAITING_APPROVAL",
            "reason": reason,
            "variance": float(case.variance) if case.variance else 0.0,
            "confidence": float(case.confidence) if case.confidence else 0.0,
            "root_cause": case.root_cause,
            "timestamp": datetime.utcnow().isoformat(),
            "policy_applied": "variance_threshold_or_low_confidence",
        }