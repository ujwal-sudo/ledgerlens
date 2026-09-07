from datetime import datetime
from decimal import Decimal

from ..db.models import Merchant, Payment, Refund, Settlement, SettlementItem, Fee
from ..db.queries import Repository
from ..domain.calculator import FinancialCalculator


class ReconciliationResult:
    """Result of a reconciliation run — always deterministic."""

    def __init__(self, status: str, variance: Decimal, expected: Decimal, actual: Decimal):
        self.status = status  # MATCHED, MINOR_DISCREPANCY, MAJOR_DISCREPANCY
        self.variance = variance  # actual - expected
        self.expected = expected
        self.actual = actual
        self.confidence = 1.0  # Always 1.0 — deterministic, no LLM involved


class ReconciliationEngine:
    """Deterministic reconciliation engine.

    Never delegates arithmetic to LLM. All calculations use Python Decimal.
    """

    # Thresholds for discrepancy classification
    MINOR_THRESHOLD = Decimal("500.00")
    MATCH_THRESHOLD = Decimal("0.01")

    def reconcile_settlement(self, repo: Repository, settlement_id: str) -> ReconciliationResult:
        """Run full reconciliation on a settlement.

        Step 1: Load settlement + window + merchant
        Step 2: Get captured payments in window
        Step 3: Get processed refunds in window
        Step 4: Get fees for settlement
        Step 5: Calculate expected = gross - refunds - fees (Decimal, always)
        Step 6: Compare actual settlement amount vs expected
        Step 7: Classify discrepancy
        """
        settlement = repo.get_settlement(settlement_id)
        if not settlement:
            raise ValueError(f"Settlement {settlement_id} not found")

        merchant_id = settlement.merchant_id

        # Step 1: Get captured payments in the settlement window
        payments_in_window = repo.get_captured_payments_in_window(
            merchant_id, settlement.window_start, settlement.cutoff_at
        )

        # Step 2: Get processed refunds in the settlement window
        refunds_in_window = repo.get_refunds_by_settlement_window(
            merchant_id, settlement.cutoff_at, status="processed"
        )

        # Step 3: Get fees for this settlement
        fees = repo.get_fees_for_settlement(settlement_id)

        # Step 4: Deterministic calculation — LLM NEVER touches arithmetic
        calculation = FinancialCalculator.calculate_expected_settlement(
            payments_in_window, refunds_in_window, fees
        )

        expected = Decimal(str(calculation["result"]))
        actual = Decimal(str(settlement.amount))

        # Step 5: Calculate variance
        variance = FinancialCalculator.calculate_variance(expected, actual)

        # Step 6: Classify discrepancy
        result = self._classify(variance)

        return ReconciliationResult(
            status=result,
            variance=variance,
            expected=expected,
            actual=actual,
        )

    def _classify(self, variance: Decimal) -> str:
        """Classify the discrepancy status based on variance amount."""
        abs_var = abs(variance)
        if abs_var < self.MATCH_THRESHOLD:
            return "MATCHED"
        elif abs_var < self.MINOR_THRESHOLD:
            return "MINOR_DISCREPANCY"
        else:
            return "MAJOR_DISCREPANCY"

    def detect_unmatched_payments(self, repo: Repository, settlement_id: str) -> list:
        """Find payments in window that are NOT in settlement items.

        These are payments captured in the window but not deducted from the
        settlement — a common source of discrepancy.
        """
        settlement = repo.get_settlement(settlement_id)
        items = repo.get_settlement_items(settlement_id)
        item_payment_ids = {i.payment_id for i in items if i.payment_id}

        payments_in_window = repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        unmatched = [
            p for p in payments_in_window
            if p.payment_id not in item_payment_ids
        ]
        return unmatched

    def detect_unmatched_refunds(self, repo: Repository, settlement_id: str) -> list:
        """Find processed refunds NOT in settlement deduction items."""
        items = repo.get_settlement_items(settlement_id)
        deducted_refund_ids = {i.payment_id for i in items if i.payment_id}

        refunds = repo.get_refunds_by_settlement_window(
            settlement.merchant_id, settlement.cutoff_at, status="processed"
        )

        unmatched = [r for r in refunds if r.refund_id not in deducted_refund_ids and r.payment_id not in deducted_refund_ids]
        return unmatched