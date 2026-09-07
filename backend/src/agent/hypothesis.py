from dataclasses import dataclass, field
from typing import List, Optional
from decimal import Decimal

from backend.src.domain.anomaly import AnomalyRecord


@dataclass
class Hypothesis:
    """A competing causal hypothesis in an investigation.

    The agent forms 2-4 competing hypotheses per case and scores them
    against evidence. Hypotheses with contradicting evidence are rejected.
    """

    hypothesis_id: str
    hypothesis_type: str  # 'REFUND_AFTER_CUTOFF' | 'FEE_ADJUSTMENT' | 'DUPLICATE_PAYMENT' | 'TIMING_DIFFERENCE' | 'MISSING_PAYMENT'
    description: str  # Natural language description from LLM
    evidence_for: List[str] = field(default_factory=list)  # evidence_ids that support this
    evidence_against: List[str] = field(default_factory=list)  # evidence_ids that contradict this
    confidence: float = 0.0  # 0.0–1.0
    status: str = "PENDING"  # 'PENDING' | 'SUPPORTED' | 'REJECTED' | 'CONFIRMED'


class HypothesisEngine:
    """Engine that forms, scores, and rejects competing hypotheses.

    The agent generates 2-3 competing hypotheses per case, then uses
    deterministic scoring to rank them. Hypotheses are rejected if
    contradicted by evidence.
    """

    # Hypothesis scoring: base score from supporting vs contradicting evidence
    # Plus quality weights for evidence source types
    QUALITY_WEIGHTS = {
        "database_record": 1.0,
        "calculation": 1.0,
        "policy_document": 0.9,
        "agent_inference": 0.6,
        "none": 0.5,
    }

    def form_hypotheses(
        self,
        anomaly_type: str,
        case_context: dict,
    ) -> List[Hypothesis]:
        """Form 2-3 competing causal hypotheses for a given anomaly.

        The LLM generates natural language descriptions; the hypothesis
        types are domain-specific (refund after cutoff, fee adjustment,
        duplicate payment, etc.).
        """
        hypothesis_types = self._get_hypothesis_types(anomaly_type)

        hypotheses = []
        for i, htype in enumerate(hypothesis_types):
            hyp = Hypothesis(
                hypothesis_id=f"H-{i+1:03d}",
                hypothesis_type=htype,
                description=self._generate_hypothesis_description(htype, case_context),
                confidence=0.0,  # Initially unknown; scored after evidence gathering
                status="PENDING",
            )
            hypotheses.append(hyp)

        # If fewer than 2 formed, pad with generic hypothesis
        while len(hypotheses) < 2:
            hyp = Hypothesis(
                hypothesis_id=f"H-{len(hypotheses)+1:03d}",
                hypothesis_type="TIMING_DIFFERENCE",
                description="Timing difference between event processing and settlement cycle",
                confidence=0.0,
                status="PENDING",
            )
            hypotheses.append(hyp)

        # Limit to 4 maximum
        return hypotheses[:4]

    def _get_hypothesis_types(self, anomaly_type: str) -> list[str]:
        """Map anomaly types to competing hypothesis types."""
        mapping = {
            "REFUND_AFTER_CUTOFF": ["REFUND_AFTER_CUTOFF", "FEE_ADJUSTMENT", "TIMING_DIFFERENCE"],
            "AMOUNT_MISMATCH": ["FEE_ADJUSTMENT", "MISSING_PAYMENT", "REFUND_AFTER_CUTOFF"],
            "MISSING_SETTLEMENT_ITEM": ["MISSING_PAYMENT", "REFUND_AFTER_CUTOFF", "DUPLICATE_PAYMENT"],
            "DUPLICATE_PAYMENT": ["DUPLICATE_PAYMENT", "FEE_ADJUSTMENT", "TIMING_DIFFERENCE"],
            "FEE_SCHEDULE_VIOLATION": ["FEE_ADJUSTMENT", "AMOUNT_MISMATCH", "TIMING_DIFFERENCE"],
        }
        return mapping.get(anomaly_type, ["FEE_ADJUSTMENT", "REFUND_AFTER_CUTOFF", "TIMING_DIFFERENCE"])

    def _generate_hypothesis_description(self, hypothesis_type: str, case_context: dict) -> str:
        """Generate natural language description for a hypothesis type."""
        descriptions = {
            "REFUND_AFTER_CUTOFF": (
                f"Refund was processed after the settlement cutoff time, "
                f"so it was excluded from the current settlement and appears in the next cycle."
            ),
            "FEE_ADJUSTMENT": (
                f"Fee amount applied to the settlement differs from the expected "
                f"fee schedule rate, causing a variance in the net settlement amount."
            ),
            "DUPLICATE_PAYMENT": (
                f"Two payment records with the same amount and merchant exist within "
                f"a 60-second window, potentially causing over-counting in the settlement."
            ),
            "TIMING_DIFFERENCE": (
                f"Transaction timestamps fall across settlement cycle boundaries, "
                f"causing a payment to appear in the wrong settlement period."
            ),
            "MISSING_PAYMENT": (
                f"A payment that should have been included in the settlement was "
                f"omitted from the settlement items, resulting in a shortfall."
            ),
        }
        return descriptions.get(
            hypothesis_type,
            "Alternative causal explanation for the observed financial discrepancy.",
        )

    def score_hypothesis(self, hypothesis: Hypothesis, evidence: List[dict]) -> float:
        """Score a hypothesis based on supporting and contradicting evidence.

        Deterministic scoring:
        - If evidence contradicts with no support → hard reject (0.0)
        - Base score = supporting / (supporting + contradicting)
        - Weighted by evidence quality (source reliability)
        """
        supporting = len(hypothesis.evidence_for)
        contradicting = len(hypothesis.evidence_against)

        # Hard reject: contradicted with no supporting evidence
        if contradicting > 0 and supporting == 0:
            return 0.0

        # Base score from evidence balance
        base_score = supporting / max(supporting + contradicting, 1)

        # Weight by evidence quality
        weighted_sum = 0.0
        for eid in hypothesis.evidence_for:
            # Find the evidence item and get its source type
            quality = self.QUALITY_WEIGHTS.get("agent_inference", 0.5)  # default
            for ev in evidence:
                if ev.get("evidence_id") == eid:
                    quality = self.QUALITY_WEIGHTS.get(ev.get("source_type", "agent_inference"), 0.5)
                    break
            weighted_sum += quality

        # Final score: base * (weighted supporting / supporting)
        # If no supporting evidence, we already hard-rejected above
        if supporting > 0:
            final = base_score * (weighted_sum / max(supporting, 1))
        else:
            final = base_score

        # Cap at 1.0
        return min(round(final, 4), 1.0)

    def reject_hypothesis(self, hypothesis: Hypothesis, evidence_id: str, reason: str) -> Hypothesis:
        """Reject a hypothesis by adding evidence against it."""
        hypothesis.evidence_against.append(evidence_id)
        # Lower confidence when rejected
        if hypothesis.status == "PENDING":
            hypothesis.status = "REJECTED"
            hypothesis.confidence = 0.0
        return hypothesis

    def confirm_hypothesis(self, hypothesis: Hypothesis, evidence_id: str) -> Hypothesis:
        """Confirm a hypothesis by adding evidence for it."""
        hypothesis.evidence_for.append(evidence_id)
        hypothesis.status = "CONFIRMED"
        # Increase confidence but cap at 1.0
        hypothesis.confidence = min(round(hypothesis.confidence + 0.15, 4), 1.0)
        return hypothesis

    def get_top_hypothesis(self, hypotheses: List[Hypothesis]) -> Optional[Hypothesis]:
        """Return the hypothesis with the highest confidence score.

        If no hypothesis has confidence >= 0.70, return None → case escalates.
        """
        if not hypotheses:
            return None

        # Sort by confidence descending
        sorted_hyp = sorted(hypotheses, key=lambda h: h.confidence, reverse=True)
        top = sorted_hyp[0]

        # If top hypothesis has insufficient confidence, case escalates
        if top.confidence < 0.70:
            return None

        return top