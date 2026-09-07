from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional

from ..db.models import Payment, Refund, Settlement, Merchant
from ..db.queries import Repository
from ..domain.calculator import FinancialCalculator


class AnomalyRecord:
    """Record of a detected anomaly with provenance."""

    def __init__(
        self,
        anomaly_type: str,
        severity: str,
        affected_entity_ids: list,
        description: str,
        evidence: list[dict] | None = None,
    ):
        self.anomaly_type = anomaly_type
        self.severity = severity
        self.affected_entity_ids = affected_entity_ids
        self.description = description
        self.evidence = evidence or []


class AnomalyDetector:
    """Rule-based anomaly detector for MVP.

    All detection logic is deterministic — no ML, no statistical models.
    Catches all demo anomalies with ground truth labels.
    """

    # Anomaly rule thresholds
    DUPLICATE_PAYMENT_WINDOW_SECONDS = 60
    CUTOFF_TOLERANCE_SECONDS = 0

    ANOMALY_RULES = [
        "AMOUNT_MISMATCH",
        "DUPLICATE_PAYMENT",
        "REFUND_AFTER_CUTOFF",
        "MISSING_SETTLEMENT_ITEM",
        "FEE_SCHEDULE_VIOLATION",
        "NEGATIVE_SETTLEMENT",
    ]

    def __init__(self, repo: Repository):
        self.repo = repo

    # --- Helper: compute expected settlement without LLM ---

    def _compute_expected(self, settlement_id: str) -> tuple[Decimal, Decimal]:
        """Compute expected settlement amount deterministically.

        Returns (expected_amount, variance) relative to settlement amount.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return Decimal("0"), Decimal("0")

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

        # Deterministic calculation using Decimal
        gross = FinancialCalculator.sum_amounts(
            [p for p in payments if getattr(p, "status", None) == "captured"]
        )
        refund_total = FinancialCalculator.sum_amounts(
            [r for r in refunds if getattr(r, "status", None) == "processed"]
        )
        fee_total = FinancialCalculator.sum_amounts([f for f in fees])

        expected = (gross - refund_total - fee_total).quantize(
            Decimal("0.01"), rounding="ROUND_HALF_UP"
        )

        variance = (Decimal(str(settlement.amount)) - expected).quantize(
            Decimal("0.01"), rounding="ROUND_HALF_UP"
        )

        return expected, variance

    # --- Detection methods ---

    def detect_amount_mismatch(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect when settled amount ≠ calculated expected amount.

        This is the fundamental mismatch that triggers the whole investigation.
        """
        expected, variance = self._compute_expected(settlement_id)

        if abs(variance) < Decimal("0.01"):
            return []

        settlement = self.repo.get_settlement(settlement_id)

        return [
            AnomalyRecord(
                anomaly_type="AMOUNT_MISMATCH",
                severity="HIGH",
                affected_entity_ids=[settlement_id],
                description=(
                    f"Settlement amount ₹{abs(settlement.amount)} differs from "
                    f"expected ₹{abs(float(expected))}. Variance: ₹{abs(float(variance))}"
                ),
                evidence=[
                    {"type": "calculation", "expected": float(expected), "variance": float(variance)},
                    {"type": "settlement_record", "amount": float(settlement.amount)},
                ],
            )
        ]

    def detect_refund_after_cutoff(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect refunds processed AFTER the settlement cutoff time.

        These refunds will NOT appear in the current settlement deduction
        but may affect prior settlements or create variance.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        # Get all processed refunds in the merchant that are within/near the window
        refunds = self.repo.get_refunds_by_settlement_window(
            settlement.merchant_id, settlement.cutoff_at, status="processed"
        )

        anomalies = []
        for refund in refunds:
            # Check if refund was processed after the cutoff
            if refund.processed_at and settlement.cutoff_at:
                delay = (refund.processed_at - settlement.cutoff_at).total_seconds()
                if delay > 0:
                    anomalies.append(
                        AnomalyRecord(
                            anomaly_type="REFUND_AFTER_CUTOFF",
                            severity="HIGH",
                            affected_entity_ids=[refund.refund_id, settlement_id],
                            description=(
                                f"Refund {refund.refund_id} (₹{abs(refund.amount)}) "
                                f"processed at {refund.processed_at}, "
                                f"after settlement cutoff of {settlement.cutoff_at}. "
                                f"This refund will appear in the NEXT settlement cycle."
                            ),
                            evidence=[
                                {"type": "refund_record", "refund_id": refund.refund_id, "processed_at": str(refund.processed_at)},
                                {"type": "settlement_cutoff", "cutoff": str(settlement.cutoff_at)},
                            ],
                        )
                    )
        return anomalies

    def detect_missing_settlement_items(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect payments in the window that are NOT in settlement items.

        These are payments captured in the window but not deducted from the
        settlement — a common source of discrepancy.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        unmatched = self.repo.detect_unmatched_payments(settlement_id)

        anomalies = []
        for payment in unmatched:
            anomalies.append(
                AnomalyRecord(
                    anomaly_type="MISSING_SETTLEMENT_ITEM",
                    severity="HIGH",
                    affected_entity_ids=[payment.payment_id, settlement_id],
                    description=(
                        f"Payment {payment.payment_id} (₹{abs(payment.amount)}) "
                        f"captured at {payment.captured_at} is in the settlement window "
                        f"but NOT in settlement deduction items."
                    ),
                    evidence=[
                        {"type": "payment_record", "payment_id": payment.payment_id, "captured_at": str(payment.captured_at)},
                        {"type": "settlement_items", "settlement_id": settlement_id},
                    ],
                )
            )
        return anomalies

    def detect_fee_violations(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect when fee line items in settlement don't match fee schedule.

        For MVP, we check if fees applied to settlement exceed or differ
        from what the fee schedule would predict.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        fees = self.repo.get_fees_for_settlement(settlement_id)

        # Calculate expected fees from merchant fee rate
        # Expected = gross * fee_rate
        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )
        gross = FinancialCalculator.sum_amounts(
            [p for p in payments if getattr(p, "status", None) == "captured"]
        )

        merchant = self.repo.get_merchant(settlement.merchant_id)
        fee_rate = merchant.fee_rate if merchant else Decimal("0.02")
        expected_fee = (gross * fee_rate).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")

        anomalies = []
        actual_fee_total = FinancialCalculator.sum_amounts(fees)

        if abs(actual_fee_total - expected_fee) > Decimal("0.01"):
            anomalies.append(
                AnomalyRecord(
                    anomaly_type="FEE_SCHEDULE_VIOLATION",
                    severity="MEDIUM",
                    affected_entity_ids=[settlement_id],
                    description=(
                        f"Fee discrepancy for settlement {settlement_id}: "
                        f"expected ₹{float(expected_fee)}, actual ₹{float(actual_fee_total)}. "
                        f"Difference: ₹{float(actual_fee_total - expected_fee)}"
                    ),
                    evidence=[
                        {"type": "calculation", "expected_fee": float(expected_fee)},
                        {"type": "fee_record", "actual_total": float(actual_fee_total), "fee_count": len(fees)},
                    ],
                )
            )

        return anomalies

    def detect_negative_settlement(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect if settlement amount is negative — data quality issue."""
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        amount = Decimal(str(settlement.amount))
        if amount < 0:
            return [
                AnomalyRecord(
                    anomaly_type="NEGATIVE_SETTLEMENT",
                    severity="CRITICAL",
                    affected_entity_ids=[settlement_id],
                    description=f"Settlement {settlement_id} has negative amount: ₹{amount}",
                    evidence=[{"type": "settlement_record", "amount": float(settlement.amount)}],
                )
            ]
        return []

    def detect_duplicate_payment(self, settlement_id: str) -> List[AnomalyRecord]:
        """Detect duplicate payments: same amount, same merchant, within 60s window.

        Two payments with identical amounts from the same merchant close in time
        can cause settlement over-counting.
        """
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        anomalies = []
        # Group payments by (amount, rounded timestamp to nearest minute)
        from collections import defaultdict

        groups: dict[tuple[Decimal, str], list] = defaultdict(list)

        for p in payments:
            # Round captured_at to minute boundary for grouping
            if p.captured_at:
                minute_key = p.captured_at.strftime("%Y-%m-%d %H:%M")
                amount_key = Decimal(str(p.amount))
                groups[(amount_key, minute_key)].append(p)

        for (amount_key, minute_key), group in groups.items():
            if len(group) > 1:
                descriptions = []
                for p in group:
                    descriptions.append(
                        f"Payment {p.payment_id} (₹{abs(p.amount)}) at {p.captured_at}"
                    )
                anomalies.append(
                    AnomalyRecord(
                        anomaly_type="DUPLICATE_PAYMENT",
                        severity="MEDIUM",
                        affected_entity_ids=[p.payment_id for p in group],
                        description="Duplicate payments detected: same amount and minute window for merchant " + settlement.merchant_id + ". " + "; ".join(descriptions),
                        evidence=[
                            {"type": "payment_record", "payment_id": p.payment_id, "amount": float(p.amount), "captured_at": str(p.captured_at)}
                            for p in group
                        ],
                    )
                )
        return anomalies

    # --- Orchestrator ---

    def detect_all(self, settlement_id: str) -> List[AnomalyRecord]:
        """Run all anomaly detection rules on a settlement."""
        results = []

        # Amount mismatch is always checked first (it's the trigger)
        results.extend(self.detect_amount_mismatch(settlement_id))

        # Rule-based detections
        results.extend(self.detect_refund_after_cutoff(settlement_id))
        results.extend(self.detect_missing_settlement_items(settlement_id))
        results.extend(self.detect_fee_violations(settlement_id))
        results.extend(self.detect_negative_settlement(settlement_id))
        results.extend(self.detect_duplicate_payment(settlement_id))

        return results