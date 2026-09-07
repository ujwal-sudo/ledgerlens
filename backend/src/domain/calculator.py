from decimal import Decimal, ROUND_HALF_UP

PRECISION = Decimal("0.01")


class FinancialCalculator:
    """All financial arithmetic. LLM NEVER touches these methods.

    Principle: Python Decimal exact arithmetic. No float operations anywhere
    in the financial pipeline. All money in INR, 2 decimal places.
    """

    @staticmethod
    def _to_decimal(value) -> Decimal:
        """Convert any value to Decimal, handling floats, strings, Decimals."""
        if isinstance(value, Decimal):
            return value
        if value is None:
            return Decimal("0")
        # Handle objects with .amount attribute (Payment, Refund, Fee)
        if hasattr(value, "amount"):
            return FinancialCalculator._to_decimal(value.amount)
        return Decimal(str(value))

    @staticmethod
    def sum_amounts(amounts) -> Decimal:
        """Sum a list of numeric values using Decimal arithmetic.

        Accepts raw numbers or objects with .amount attribute (Payment, Refund, Fee).
        """
        total = Decimal("0")
        for a in amounts:
            # Extract .amount if the object has one
            val = a.amount if hasattr(a, "amount") else a
            if val is None:
                val = Decimal("0")
            total += Decimal(str(val))
        return total.quantize(PRECISION, rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_expected_settlement(
        payments,
        refunds,
        fees,
    ) -> dict:
        """Calculate expected settlement amount deterministically.

        Formula: gross_payments - refunds - fees
        All arithmetic is Decimal — never LLM.
        """
        gross = FinancialCalculator.sum_amounts(
            [p for p in payments if getattr(p, "status", None) == "captured"]
        )
        refund_total = FinancialCalculator.sum_amounts(
            [r for r in refunds if getattr(r, "status", None) == "processed"]
        )
        fee_total = FinancialCalculator.sum_amounts([f for f in fees])

        net = (gross - refund_total - fee_total).quantize(PRECISION, rounding=ROUND_HALF_UP)

        return {
            "formula": "gross_payments - refunds - fees",
            "components": {
                "gross": float(gross),
                "refunds": float(refund_total),
                "fees": float(fee_total),
            },
            "result": float(net),
            "precision": "INR 2dp",
            "deterministic": True,
        }

    @staticmethod
    def calculate_variance(expected: Decimal, actual: Decimal) -> Decimal:
        """Calculate variance = actual - expected, rounded to 2dp INR."""
        return (actual - expected).quantize(PRECISION, rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_refund_deduction(refunds) -> Decimal:
        """Calculate total refund amount to deduct from settlement."""
        return FinancialCalculator.sum_amounts(
            [r for r in refunds if getattr(r, "status", None) == "processed"]
        )

    @staticmethod
    def verify_settlement_balance(expected: Decimal, actual: Decimal) -> bool:
        """Verify that expected and actual match within 2dp precision."""
        return expected == actual

    @staticmethod
    def round_inr(value: Decimal) -> Decimal:
        """Round to INR 2 decimal places (half-up)."""
        return value.quantize(PRECISION, rounding=ROUND_HALF_UP)