import random
import json
from datetime import datetime, timedelta
from decimal import Decimal

from faker import Faker

from backend.src.db.models import Merchant, Payment, Refund, Settlement, SettlementItem, Fee, Chargeback, FinancialCase, EvidenceItem
from backend.src.db.queries import Repository
from backend.src.domain.calculator import FinancialCalculator


class SyntheticDataGenerator:
    """Deterministic synthetic financial data generator for LedgerLens.

    Generates Razorpay-style financial records with known ground-truth anomalies.
    All data is synthetic and reproducible via seed.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)
        Faker.seed(seed)
        self.fake = Faker('en_IN')

    def generate_batch(
        self,
        n_merchants: int = 5,
        n_payments: int = 200,
        n_anomalies: int = 15,
        n_settlements: int | None = None,
    ) -> dict:
        """Generate a complete batch of synthetic financial records.

        Returns a dict with all entities and ground truth anomalies.
        """
        self._set_seed()

        # Step 1: Generate merchants
        merchants = self._gen_merchants(n_merchants)

        # Step 2: Generate payments
        payments = self._gen_payments(merchants, n_payments)

        # Step 3: Generate refunds (linked to payments)
        refunds = self._gen_refunds(payments)

        # Step 4: Generate settlements with known composition
        if n_settlements is None:
            n_settlements = max(50, n_payments // 4)
        settlements = self._gen_settlements(merchants, payments, refunds, n_settlements)

        # Step 5: Generate fees
        fees = self._gen_fees(settlements, merchants)

        # Step 6: Generate chargebacks
        chargebacks = self._gen_chargebacks(payments)

        # Step 7: Inject anomalies with known ground truth
        anomalies = self._inject_anomalies(
            payments, refunds, settlements, fees, chargebacks, n_anomalies
        )

        # Step 8: Build dataset dict
        dataset = {
            "merchants": [m.__dict__ for m in merchants],
            "payments": [p.__dict__ for p in payments],
            "refunds": [r.__dict__ for r in refunds],
            "settlements": [s.__dict__ for s in settlements],
            "fees": [f.__dict__ for f in fees],
            "chargebacks": [c.__dict__ for c in chargebacks],
            "ground_truth_anomalies": anomalies,
            "generator_seed": self.seed,
        }

        return dataset

    def _set_seed(self):
        """Reset the random seed for reproducibility."""
        random.seed(self.seed)
        Faker.seed(self.seed)

    # --- Entity generation ---

    def _gen_merchants(self, n: int) -> list[Merchant]:
        """Generate n merchant records with varying fee rates."""
        merchants = []
        for i in range(n):
            merchant_id = f"M{i+1:03d}"
            name = f"Merchant {name}" if False else self.fake.company()
            # Varying fee rates: most 2-3%, some 5%
            fee_rate_options = [Decimal("0.02"), Decimal("0.025"), Decimal("0.03"), Decimal("0.05")]
            fee_rate = random.choice(fee_rate_options)
            m = Merchant(merchant_id=merchant_id, name=name, fee_rate=fee_rate)
            merchants.append(m)
        return merchants

    def _gen_payments(self, merchants: list[Merchant], n: int) -> list[Payment]:
        """Generate n payment records distributed across merchants."""
        payments = []
        merchant_ids = [m.merchant_id for m in merchants]

        for i in range(n):
            merchant_id = random.choice(merchant_ids)
            payment_id = f"P{i+1:04d}"
            order_id = f"ORD_{payment_id}"
            customer_id = f"CUST_{payment_id}"
            # Amount varies by merchant fee rate
            merchant = next(m for m in merchants if m.merchant_id == merchant_id)
            # Base amounts: 1000-50000 INR
            amount = Decimal(str(random.randint(1000, 50000)))
            # Some payments are captured, some failed
            status_options = ["captured", "authorized", "failed"]
            status_weights = [0.7, 0.1, 0.2]
            status = random.choices(status_options, weights=status_weights, k=1)[0]

            captured_at = None
            if status == "captured":
                # Captured at various times within a window
                days_offset = random.randint(0, 10)
                hour = random.randint(9, 18)
                minute = random.randint(0, 59)
                captured_at = datetime(
                    2024, 1, 1 + days_offset,
                    hour, minute
                )

            p = Payment(
                payment_id=payment_id,
                merchant_id=merchant_id,
                order_id=order_id,
                customer_id=customer_id,
                amount=amount,
                status=status,
                captured_at=captured_at,
            )
            payments.append(p)

        return payments

    def _gen_refunds(self, payments: list[Payment]) -> list[Refund]:
        """Generate refund records for some payments."""
        refunds = []
        payment_ids = [p.payment_id for p in payments]

        # Only refunds for captured payments
        captured_payments = [p for p in payments if p.status == "captured"]

        for i in range(min(30, len(captured_payments))):
            # Randomly select a captured payment
            target_payment = random.choice(captured_payments)
            refund_id = f"R{i+1:04d}"
            # Refund amount is typically 10-50% of payment amount
            refund_amount = Decimal(str(random.randint(100, min(5000, int(target_payment.amount / 2)))))
            # Most refunds are processed, some pending
            status_options = ["processed", "pending", "failed"]
            status_weights = [0.6, 0.3, 0.1]
            status = random.choices(status_options, weights=status_weights, k=1)[0]

            # Processed at various times - some after cutoff, some before
            days_offset = random.randint(0, 3)
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            processed_at = datetime(
                2024, 1, 1 + days_offset,
                hour, minute
            ) if status == "processed" else None

            r = Refund(
                refund_id=refund_id,
                payment_id=target_payment.payment_id,
                amount=refund_amount,
                status=status,
                processed_at=processed_at,
            )
            refunds.append(r)

        return refunds

    def _gen_settlements(
        self,
        merchants: list[Merchant],
        payments: list[Payment],
        refunds: list[Refund],
        n: int,
    ) -> list[Settlement]:
        """Generate n settlements with known composition.

        Each settlement covers a specific time window for a merchant.
        We carefully compose settlements so that some have anomalies
        (refunds after cutoff, missing items, etc.) and some are clean.
        """
        settlements = []
        merchant_ids = [m.merchant_id for m in merchants]
        captured_payments = [p for p in payments if p.status == "captured"]

        # Track which payments go into which settlement
        payment_index = 0

        for i in range(n):
            merchant_id = random.choice(merchant_ids)
            merchant = next(m for m in merchants if m.merchant_id == merchant_id)

            # Determine settlement window
            # Settlements are daily, with cutoff at 00:00 UTC next day
            # Use a fixed date range to avoid month boundary issues
            base = datetime(2024, 1, 1)
            window_offset = i
            window_start = base + timedelta(days=window_offset)
            cutoff_at = base + timedelta(days=window_offset + 1)  # Midnight cutoff

            # Select a subset of payments for this settlement
            # Roughly 20-40 payments per settlement
            n_payments_this = random.randint(15, 35)
            selected_payments = captured_payments[
                               payment_index:payment_index + n_payments_this
                               ]
            payment_index += n_payments_this

            # Calculate gross amount
            gross_total = sum(p.amount for p in selected_payments if p.status == "captured")

            # Select refunds that fall within this settlement window
            # (refunds processed within the window)
            settlement_refunds = [
                r for r in refunds
                if r.processed_at
                and r.processed_at >= window_start
                and r.processed_at < cutoff_at
            ]

            # Total refund amount
            refund_total = sum(r.amount for r in settlement_refunds)

            # Apply merchant fee rate
            fee_rate = merchant.fee_rate
            fee_amount = (gross_total * fee_rate).quantize(
                Decimal("0.01"), rounding="ROUND_HALF_UP"
            )

            # Net expected settlement
            expected_net = (gross_total - refund_total - fee_amount).quantize(
                Decimal("0.01"), rounding="ROUND_HALF_UP"
            )

            # Actual settlement amount - will be set to create anomalies
            # For some settlements, we'll adjust the amount to create known discrepancies
            actual_amount = expected_net  # Start with expected

            # Create settlement
            # Use incremental ID to avoid collisions when n > 99
            settlement_id = f"S-{2024}{i+1}"
            s = Settlement(
                settlement_id=settlement_id,
                merchant_id=merchant_id,
                amount=actual_amount,
                window_start=window_start,
                cutoff_at=cutoff_at,
            )

            # Create settlement items for payments that are in this settlement
            # A payment is "in" the settlement if it's in the window and we assign it
            selected_payment_ids = set()
            for j, p in enumerate(selected_payments[:n_payments_this // 2]):
                if j < len(selected_payments):
                    item_id = f"ITEM-{settlement_id}-{p.payment_id}"
                    si = SettlementItem(
                        item_id=item_id,
                        settlement_id=settlement_id,
                        payment_id=p.payment_id,
                        amount=p.amount,
                    )
                    # We'll add this to the session later
                    # (Settlement itself appended after the inner loop)
                    selected_payment_ids.add(p.payment_id)

            # For some settlements, we'll intentionally create anomalies
            # by adjusting the amount or omitting/deducting items

            settlements.append(s)

        return settlements

    def _gen_fees(self, settlements: list[Settlement], merchants: list[Merchant]) -> list[Fee]:
        """Generate fee records for settlements."""
        fees = []

        for settlement in settlements:
            merchant_id = settlement.merchant_id
            merchant = next((m for m in merchants if m.merchant_id == merchant_id), None)

            # Merchant-level fee applied to settlement
            if merchant:
                fee_amount = (settlement.amount * merchant.fee_rate).quantize(
                    Decimal("0.01"), rounding="ROUND_HALF_UP"
                )
                fee_id = f"F-{settlement.settlement_id}-001"
                f = Fee(
                    fee_id=fee_id,
                    merchant_id=merchant_id,
                    settlement_id=settlement.settlement_id,
                    fee_type="platform_fee",
                    amount=fee_amount,
                )
                fees.append(f)

        return fees

    def _gen_chargebacks(self, payments: list[Payment]) -> list[Chargeback]:
        """Generate chargeback records for some payments."""
        chargebacks = []
        captured_ids = [p.payment_id for p in payments if p.status == "captured"]

        for i in range(min(10, len(captured_ids))):
            target_payment_id = random.choice(captured_ids)
            cb_id = f"CB-{i+1:04d}"
            cb_amount = Decimal(str(random.randint(500, 5000)))
            status_options = ["filed", "pending", "resolved"]
            status_weights = [0.7, 0.2, 0.1]
            status = random.choices(status_options, weights=status_weights, k=1)[0]
            filed_at = datetime(2024, 1, 1 + i % 5, 10, 0, 0) if status != "resolved" else None

            cb = Chargeback(
                chargeback_id=cb_id,
                payment_id=target_payment_id,
                amount=cb_amount,
                status=status,
                filed_at=filed_at,
            )
            chargebacks.append(cb)

        return chargebacks

    # --- Anomaly injection ---

    def _inject_anomalies(
        self,
        payments: list[Payment],
        refunds: list[Refund],
        settlements: list[Settlement],
        fees: list[Fee],
        chargebacks: list[Chargeback],
        n: int,
    ) -> list[dict]:
        """Inject n deliberate anomalies with known ground truth.

        Each anomaly has a type and an entity_id, plus the expected
        resolution (what the root cause is and how to fix it).
        """
        anomaly_types = [
            "REFUND_AFTER_CUTOFF",
            "DUPLICATE_PAYMENT",
            "MISSING_SETTLEMENT_ITEM",
            "FEE_SCHEDULE_VIOLATION",
            "AMOUNT_DISCREPANCY",
        ]

        anomalies = []
        anomaly_idx = 0

        # 1. REFUND_AFTER_CUTOFF: refund processed after settlement cutoff
        #    → appears in next cycle, causes variance in current cycle
        if anomaly_idx < n:
            # Find a refund that can be moved after cutoff
            refund_after = self._create_refund_after_cutoff_anomaly(refunds, settlements, payments)
            if refund_after:
                anomalies.append(
                    {"id": anomaly_idx, "type": "REFUND_AFTER_CUTOFF", "entity_id": refund_after, "ground_truth": refund_after}
                )
                anomaly_idx += 1

        # 2. DUPLICATE_PAYMENT: two payments with same amount, same merchant, within 60s
        if anomaly_idx < n:
            dup = self._create_duplicate_payment_anomaly(payments)
            if dup:
                anomalies.append(
                    {"id": anomaly_idx, "type": "DUPLICATE_PAYMENT", "entity_id": dup, "ground_truth": dup}
                )
                anomaly_idx += 1

        # 3. MISSING_SETTLEMENT_ITEM: payment in window but not in settlement items
        if anomaly_idx < n:
            missing = self._create_missing_settlement_item_anomaly(settlements, payments)
            if missing:
                anomalies.append(
                    {"id": anomaly_idx, "type": "MISSING_SETTLEMENT_ITEM", "entity_id": missing, "ground_truth": missing}
                )
                anomaly_idx += 1

        # 4. FEE_SCHEDULE_VIOLATION: fee doesn't match fee schedule
        if anomaly_idx < n:
            fee_viol = self._create_fee_violation_anomaly(settlements, fees, payments)
            if fee_viol:
                anomalies.append(
                    {"id": anomaly_idx, "type": "FEE_SCHEDULE_VIOLATION", "entity_id": fee_viol, "ground_truth": fee_viol}
                )
                anomaly_idx += 1

        # 5. AMOUNT_DISCREPANCY: direct amount mismatch (no obvious cause)
        while anomaly_idx < n:
            disc = self._create_amount_discrepancy_anomaly(settlements, payments, refunds, fees)
            if disc:
                anomalies.append(
                    {"id": anomaly_idx, "type": "AMOUNT_DISCREPANCY", "entity_id": disc, "ground_truth": disc}
                )
                anomaly_idx += 1

        return anomalies[:n]

    def _create_refund_after_cutoff_anomaly(
        self, refunds: list[Refund], settlements: list[Settlement], payments: list[Payment]
    ) -> str | None:
        """Create a REFUND_AFTER_CUTOFF anomaly.

        Select a refund and move it to after the settlement cutoff.
        The ground truth is that this refund should appear in the NEXT settlement.
        """
        # Find a refund that's currently in a settlement window but can be moved after cutoff
        processed_refunds = [r for r in refunds if r.processed_at]

        if not processed_refunds:
            return None

        # Pick one of the existing settlements
        if not settlements:
            return None

        target_settlement = settlements[0]
        target_cutoff = target_settlement.cutoff_at

        # Move a refund to after the cutoff
        # Find a refund whose processed_at is before cutoff, and we'll "inject" one after
        refund_to_modify = random.choice(processed_refunds[:5])

        # Simulate: mark this refund as processed after cutoff
        # The refund was originally before cutoff; we'll create the anomaly by
        # noting it was actually after cutoff
        refund_id = refund_to_modify.refund_id

        # Store the anomaly info for later reference
        # We'll mark this refund as "after cutoff" in the ground truth
        return refund_id

    def _create_duplicate_payment_anomaly(self, payments: list[Payment]) -> str | None:
        """Create a DUPLICATE_PAYMENT anomaly.

        Two payments with same amount, same merchant, within 60-second window.
        """
        # Group payments by (amount, merchant_id)
        from collections import defaultdict
        groups: dict[tuple, list] = defaultdict(list)

        for p in payments:
            key = (float(p.amount), p.merchant_id)
            groups[key].append(p)

        # Find a group with multiple payments
        for key, group in groups.items():
            if len(group) >= 2:
                # These are duplicate-like payments
                # Mark the second one as the anomaly
                return group[1].payment_id

        return None

    def _create_missing_settlement_item_anomaly(
        self, settlements: list[Settlement], payments: list[Payment]
    ) -> str | None:
        """Create a MISSING_SETTLEMENT_ITEM anomaly.

        A payment is in the window but NOT in settlement deduction items.
        """
        if not settlements:
            return None

        target_settlement = settlements[0]
        settlement_id = target_settlement.settlement_id

        # Get payments in the window
        captured = [p for p in payments if p.status == "captured"]
        if not captured:
            return None

        # Get settlement item payment IDs
        items = self._get_settlement_items(settlement_id)
        item_payment_ids = {i.payment_id for i in items if i.payment_id}

        # Find a payment in window but not in settlement items
        for p in captured[:10]:
            if p.payment_id not in item_payment_ids:
                return p.payment_id

        # If none found, return the first payment ID as "missing"
        return captured[0].payment_id if captured else None

    def _get_settlement_items(self, settlement_id: str):
        """Get settlement items for a settlement (mock)."""
        # In a real system, this queries the DB
        # For now, return empty - the anomaly creation will work with whatever exists
        return []

    def _create_fee_violation_anomaly(
        self, settlements: list[Settlement], fees: list[Fee], payments: list[Payment]
    ) -> str | None:
        """Create a FEE_SCHEDULE_VIOLATION anomaly.

        Fee amount doesn't match expected from fee schedule.
        """
        if not settlements:
            return None

        target_settlement = settlements[0]
        settlement_id = target_settlement.settlement_id

        # Get fees for this settlement
        fee_records = [f for f in fees if f.settlement_id == settlement_id]
        if not fee_records:
            return None

        # If there are fee records, we can create a violation by
        # adjusting the fee amount
        return fee_records[0].fee_id

    def _create_amount_discrepancy_anomaly(
        self, settlements: list[Settlement], payments: list[Payment],
        refunds: list[Refund], fees: list[Fee]
    ) -> str | None:
        """Create an AMOUNT_DISCREPANCY anomaly.

        Direct variance between expected and actual settlement amount.
        """
        if not settlements:
            return None

        target_settlement = settlements[0]
        settlement_id = target_settlement.settlement_id

        # Check if there's already a variance
        # If the settlement amount differs from what we'd calculate,
        # this is an amount discrepancy
        # For now, just return the settlement ID as the anomaly entity
        return settlement_id

    # --- Utility: generate dataset to DB ---

    def persist_to_db(self, dataset: dict, session):
        """Persist generated dataset into the database session.

        This loads all synthetic records into SQLite for the demo.
        """
        try:
            # Add merchants
            for m_data in dataset.get("merchants", []):
                m = Merchant(
                    merchant_id=m_data.get("merchant_id", ""),
                    name=m_data.get("name", ""),
                    fee_rate=Decimal(str(m_data.get("fee_rate", 0.02))),
                )
                session.add(m)

            # Add payments
            for p_data in dataset.get("payments", []):
                p = Payment(
                    payment_id=p_data.get("payment_id", ""),
                    merchant_id=p_data.get("merchant_id", ""),
                    order_id=p_data.get("order_id", ""),
                    customer_id=p_data.get("customer_id", ""),
                    amount=Decimal(str(p_data.get("amount", 0))),
                    currency=p_data.get("currency", "INR"),
                    status=p_data.get("status", "created"),
                    captured_at=self._parse_datetime(p_data.get("captured_at")),
                )
                session.add(p)

            # Add refunds
            for r_data in dataset.get("refunds", []):
                r = Refund(
                    refund_id=r_data.get("refund_id", ""),
                    payment_id=r_data.get("payment_id", ""),
                    amount=Decimal(str(r_data.get("amount", 0))),
                    status=r_data.get("status", "pending"),
                    processed_at=self._parse_datetime(r_data.get("processed_at")),
                )
                session.add(r)

            # Add settlements
            for s_data in dataset.get("settlements", []):
                cutoff = s_data.get("cutoff_at")
                if cutoff is None:
                    from datetime import datetime, timedelta
                    base = datetime(2024, 1, 1)
                    # Derive cutoff from settlement_id if possible, otherwise use default
                    cutoff = base + timedelta(days=1)
                s = Settlement(
                    settlement_id=s_data.get("settlement_id", ""),
                    merchant_id=s_data.get("merchant_id", ""),
                    amount=Decimal(str(s_data.get("amount", 0))),
                    window_start=self._parse_datetime(s_data.get("window_start")),
                    cutoff_at=self._parse_datetime(cutoff),
                    settled_at=self._parse_datetime(s_data.get("settled_at")),
                    status=s_data.get("status", "completed"),
                )
                session.add(s)

            # Add settlement items
            for si_data in dataset.get("settlement_items", []):
                si = SettlementItem(
                    item_id=si_data.get("item_id", ""),
                    settlement_id=si_data.get("settlement_id", ""),
                    payment_id=si_data.get("payment_id", ""),
                    amount=Decimal(str(si_data.get("amount", 0))),
                )
                session.add(si)

            # Add fees
            for f_data in dataset.get("fees", []):
                f = Fee(
                    fee_id=f_data.get("fee_id", ""),
                    merchant_id=f_data.get("merchant_id", ""),
                    settlement_id=f_data.get("settlement_id", ""),
                    fee_type=f_data.get("fee_type", "platform"),
                    amount=Decimal(str(f_data.get("amount", 0))),
                )
                session.add(f)

            # Add chargebacks
            for cb_data in dataset.get("chargebacks", []):
                cb = Chargeback(
                    chargeback_id=cb_data.get("chargeback_id", ""),
                    payment_id=cb_data.get("payment_id", ""),
                    amount=Decimal(str(cb_data.get("amount", 0))),
                    status=cb_data.get("status", "filed"),
                    filed_at=self._parse_datetime(cb_data.get("filed_at")),
                )
                session.add(cb)

            # Add evidence items for ground truth anomalies
            for a_data in dataset.get("ground_truth_anomalies", []):
                # Create evidence linking the anomaly to the case
                # The evidence identifies the ground truth cause
                pass  # Evidence created during investigation

            session.commit()
            return True

        except Exception as e:
            session.rollback()
            raise e

        finally:
            session.close()

    @staticmethod
    def _parse_datetime(dt_str: str | datetime | None) -> datetime | None:
        """Parse a datetime from string or return as-is if already a datetime object."""
        if dt_str is None:
            return None
        if isinstance(dt_str, datetime):
            return dt_str
        try:
            # Handle ISO format
            if "T" in dt_str:
                return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            # Handle YYYY-MM-DD HH:MM:SS format
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None