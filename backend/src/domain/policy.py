from decimal import Decimal
from typing import Optional, Dict, Any


class PolicyDecision:
    """Result of policy engine evaluation — always deterministic."""

    def __init__(
        self,
        action: str,
        policy_name: str,
        reason: str,
        deterministic: bool = True,
        variance_threshold: Decimal = Decimal("500.00"),
        confidence_threshold: Decimal = Decimal("0.70"),
    ):
        self.action = action  # AUTO_RESOLVE | ESCALATE_TO_HUMAN | MANUAL_REVIEW
        self.policy_name = policy_name
        self.reason = reason
        self.deterministic = deterministic


class PolicyEngine:
    """Deterministic policy engine — never delegates decisions to LLM.

    Enforces automation boundaries for the investigation pipeline.
    """

    # Policy thresholds (INR amounts)
    AUTO_RESOLVE_VARIANCE_THRESHOLD = Decimal("500.00")  # ₹500
    CONFIDENCE_LOW_THRESHOLD = Decimal("0.70")  # 70%
    CONFIDENCE_GOOD_THRESHOLD = Decimal("0.85")  # 85%

    # Policy rules (evaluated in order; first match wins)
    POLICIES = [
        {
            "name": "auto_resolve_minor",
            "condition": lambda r: abs(r.variance) < PolicyEngine.AUTO_RESOLVE_VARIANCE_THRESHOLD
            and r.confidence >= PolicyEngine.CONFIDENCE_GOOD_THRESHOLD,
            "action": "AUTO_RESOLVE",
            "description": "Auto-resolve discrepancies under ₹500 with high confidence (≥85%)",
        },
        {
            "name": "escalate_major",
            "condition": lambda r: abs(r.variance) >= PolicyEngine.AUTO_RESOLVE_VARIANCE_THRESHOLD,
            "action": "ESCALATE_TO_HUMAN",
            "description": "Always escalate discrepancies ≥ ₹500",
        },
        {
            "name": "escalate_low_confidence",
            "condition": lambda r: r.confidence < PolicyEngine.CONFIDENCE_LOW_THRESHOLD,
            "action": "ESCALATE_TO_HUMAN",
            "description": "Escalate if root cause confidence is below 70%",
        },
        {
            "name": "flag_duplicate",
            "condition": lambda r: r.root_cause_type == "DUPLICATE_PAYMENT",
            "action": "ESCALATE_TO_HUMAN",
            "description": "Always escalate duplicates — financial risk too high to auto-resolve",
        },
    ]

    def evaluate(self, result) -> PolicyDecision:
        """Evaluate policies against an investigation result.

        Args:
            result: InvestigationResult object with variance, confidence, root_cause_type

        Returns:
            PolicyDecision with action, policy_name, and reason
        """
        for policy in self.POLICIES:
            if policy["condition"](result):
                return PolicyDecision(
                    action=policy["action"],
                    policy_name=policy["name"],
                    reason=policy["description"],
                    deterministic=True,  # Policy evaluation is NEVER AI
                )

        # Default: no policy matched → manual review
        return PolicyDecision(
            action="MANUAL_REVIEW",
            policy_name="default",
            reason="No policy matched — review required",
            deterministic=True,
        )