from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from ..db.models import Merchant, Payment, Refund, Settlement, SettlementItem, Fee, Chargeback, FinancialCase, EvidenceItem, AgentRun, AccountingEntry


class Repository:
    """Repository pattern for database access operations."""

    def __init__(self, session: Session):
        self.session = session

    # --- Merchant ---

    def get_merchant(self, merchant_id: str) -> Merchant | None:
        return self.session.get(Merchant, merchant_id)

    def create_merchant(self, merchant_id: str, name: str, fee_rate: Decimal = Decimal("0.02")):
        m = Merchant(merchant_id=merchant_id, name=name, fee_rate=fee_rate)
        self.session.add(m)
        return m

    # --- Payment ---

    def get_payment(self, payment_id: str) -> Payment | None:
        return self.session.get(Payment, payment_id)

    def get_payments_by_merchant(
        self, merchant_id: str, status: str | None = None, start: datetime | None = None, end: datetime | None = None
    ):
        q = self.session.query(Payment).filter(Payment.merchant_id == merchant_id)
        if status:
            q = q.filter(Payment.status == status)
        if start:
            q = q.filter(Payment.captured_at >= start)
        if end:
            q = q.filter(Payment.captured_at <= end)
        return q.order_by(Payment.captured_at.asc()).all()

    def get_captured_payments_in_window(
        self, merchant_id: str, window_start: datetime, cutoff_at: datetime
    ):
        return (
            self.session.query(Payment)
            .filter(
                Payment.merchant_id == merchant_id,
                Payment.status == "captured",
                Payment.captured_at >= window_start,
                Payment.captured_at <= cutoff_at,
            )
            .order_by(Payment.captured_at.asc())
            .all()
        )

    def get_payment_by_id(self, payment_id: str) -> Payment | None:
        return self.session.get(Payment, payment_id)

    # --- Refund ---

    def get_refund(self, refund_id: str) -> Refund | None:
        return self.session.get(Refund, refund_id)

    def get_refunds_by_merchant(
        self, merchant_id: str, end: datetime | None = None, status: str | None = None
    ):
        q = self.session.query(Refund).filter(Refund.merchant_id == merchant_id)  # type: ignore[attr-defined]
        # Note: Refund doesn't have merchant_id directly; we'll filter via payment
        if end:
            q = q.filter(Refund.processed_at <= end)
        if status:
            q = q.filter(Refund.status == status)
        return q.order_by(Refund.processed_at.asc()).all()

    def get_refunds_by_settlement_window(
        self, merchant_id: str, end: datetime, status: str = "processed"
    ):
        """Get refunds processed in the settlement window for a merchant."""
        # Refunds are linked via payments; we query payments in window, then their refunds
        from ..db.models import Payment
        payments = (
            self.session.query(Payment)
            .filter(
                Payment.merchant_id == merchant_id,
                Payment.status == "captured",
                Payment.captured_at <= end,
            )
            .all()
        )
        refund_ids = []
        for p in payments:
            refs = self.session.query(Refund).filter(
                Refund.payment_id == p.payment_id,
                Refund.status == status,
                Refund.processed_at <= end,
            ).all()
            refund_ids.extend(refund_ids)
        # Dedup
        unique_refunds = {}
        for r in refs:
            unique_refunds[r.refund_id] = r
        return list(unique_refunds.values())

    def get_refunds_by_payment(self, payment_id: str) -> list[Refund]:
        return (
            self.session.query(Refund)
            .filter(Refund.payment_id == payment_id, Refund.status == "processed")
            .all()
        )

    # --- Settlement ---

    def get_settlement(self, settlement_id: str) -> Settlement | None:
        return self.session.get(Settlement, settlement_id)

    def get_settlement_items(self, settlement_id: str):
        return (
            self.session.query(SettlementItem)
            .filter(SettlementItem.settlement_id == settlement_id)
            .all()
        )

    def get_settlement_items_payment_ids(self, settlement_id: str) -> set[str]:
        items = self.get_settlement_items(settlement_id)
        return {i.payment_id for i in items if i.payment_id}

    def create_settlement(
        self,
        settlement_id: str,
        merchant_id: str,
        amount: Decimal,
        window_start: datetime,
        cutoff_at: datetime,
    ):
        s = Settlement(
            settlement_id=settlement_id,
            merchant_id=merchant_id,
            amount=amount,
            window_start=window_start,
            cutoff_at=cutoff_at,
        )
        self.session.add(s)
        return s

    # --- Settlement Item ---

    def create_settlement_item(
        self, item_id: str, settlement_id: str, payment_id: str | None, amount: Decimal
    ):
        si = SettlementItem(item_id=item_id, settlement_id=settlement_id, payment_id=payment_id, amount=amount)
        self.session.add(si)
        return si

    # --- Fee ---

    def get_fees_for_settlement(self, settlement_id: str):
        return (
            self.session.query(Fee)
            .filter(Fee.settlement_id == settlement_id)
            .all()
        )

    def get_fees_by_merchant(self, merchant_id: str):
        return (
            self.session.query(Fee)
            .filter(Fee.merchant_id == merchant_id)
            .all()
        )

    def create_fee(
        self,
        fee_id: str,
        merchant_id: str,
        settlement_id: str | None,
        fee_type: str,
        amount: Decimal,
    ):
        f = Fee(fee_id=fee_id, merchant_id=merchant_id, settlement_id=settlement_id, fee_type=fee_type, amount=amount)
        self.session.add(f)
        return f

    # --- Chargeback ---

    def get_chargeback(self, chargeback_id: str) -> Chargeback | None:
        return self.session.get(Chargeback, chargeback_id)

    def get_chargebacks_by_payment(self, payment_id: str) -> list[Chargeback]:
        return (
            self.session.query(Chargeback)
            .filter(Chargeback.payment_id == payment_id)
            .all()
        )

    # --- Financial Case ---

    def get_case(self, case_id: str) -> FinancialCase | None:
        return self.session.get(FinancialCase, case_id)

    def create_case(
        self,
        case_id: str,
        object_type: str,
        object_id: str,
        anomaly_type: str | None = None,
    ):
        c = FinancialCase(
            case_id=case_id,
            object_type=object_type,
            object_id=object_id,
            anomaly_type=anomaly_type,
        )
        self.session.add(c)
        return c

    def update_case(
        self,
        case_id: str,
        status: str | None = None,
        root_cause: str | None = None,
        root_cause_type: str | None = None,
        confidence: Decimal | None = None,
        variance: Decimal | None = None,
    ):
        c = self.get_case(case_id)
        if status:
            c.status = status
        if root_cause:
            c.root_cause = root_cause
        if root_cause_type:
            c.root_cause_type = root_cause_type
        if confidence is not None:
            c.confidence = confidence
        if variance is not None:
            c.variance = variance
        if status in ("RESOLVED", "ESCALATED"):
            c.resolved_at = datetime.utcnow()
        return c

    # --- Evidence Item ---

    def create_evidence(
        self,
        evidence_id: str,
        case_id: str,
        claim: str,
        source_type: str,
        source_table: str | None,
        source_id: str | None,
        source_field: str | None,
        value: str,
        agent_step_id: str | None = None,
    ):
        e = EvidenceItem(
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
        self.session.add(e)
        return e

    # --- Agent Run ---

    def create_agent_run(self, case_id: str, steps_json: str) -> AgentRun:
        run_id = f"AR-{case_id.split('-')[1]}"
        ar = AgentRun(run_id=run_id, case_id=case_id, steps=steps_json, status="RUNNING")
        self.session.add(ar)
        return ar

    def complete_agent_run(self, run_id: str, completed_steps_json: str):
        ar = self.session.get(AgentRun, run_id)
        if ar:
            ar.status = "COMPLETED"
            ar.completed_at = datetime.utcnow()
            ar.steps = completed_steps_json

    def get_case_status_filter(self, status: str) -> list[dict]:
        """List all cases with a given status."""
        from ..db.models import FinancialCase
        cases = (
            self.session.query(FinancialCase)
            .filter(FinancialCase.status == status)
            .all()
        )
        return [
            {
                "case_id": c.case_id,
                "object_type": c.object_type,
                "object_id": c.object_id,
                "status": c.status,
                "anomaly_type": c.anomaly_type,
                "root_cause": c.root_cause,
                "confidence": float(c.confidence) if c.confidence else None,
                "variance": float(c.variance) if c.variance else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            }
            for c in cases
        ]

    def get_evidence_by_case(self, case_id: str) -> list[dict]:
        """Get all evidence for a case."""
        from ..db.models import EvidenceItem
        evidences = (
            self.session.query(EvidenceItem)
            .filter(EvidenceItem.case_id == case_id)
            .all()
        )
        return [
            {
                "evidence_id": e.evidence_id,
                "claim": e.claim,
                "source_type": e.source_type,
                "source_table": e.source_table,
                "source_id": e.source_id,
                "source_field": e.source_field,
                "value": e.value,
                "agent_step_id": e.agent_step_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in evidences
        ]

    # --- Accounting Entry ---

    def create_accounting_entry(
        self,
        entry_id: str,
        entity_type: str,
        entity_id: str,
        amount: Decimal,
        entry_type: str,
    ):
        ae = AccountingEntry(
            entry_id=entry_id,
            entity_type=entity_type,
            entity_id=entity_id,
            amount=amount,
            entry_type=entry_type,
            recorded_at=datetime.utcnow(),
        )
        self.session.add(ae)
        return ae