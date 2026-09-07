from sqlalchemy import (
    Column, String, DateTime, Integer, ForeignKey, Text, JSON, Boolean, Numeric
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
from decimal import Decimal

Base = declarative_base()


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id = Column(String, primary_key=True, comment="Unique merchant identifier")
    name = Column(String, nullable=False, comment="Merchant legal name")
    fee_rate = Column(Numeric(5, 4), nullable=False, default=0.02, comment="Default fee rate 2%")
    created_at = Column(DateTime, nullable=False, default=func.now())

    payments = relationship("Payment", back_populates="merchant", cascade="all, delete-orphan")
    refunds = relationship("Refund", back_populates="merchant", cascade="all, delete-orphan")
    fees = relationship("Fee", back_populates="merchant", cascade="all, delete-orphan")
    settlements = relationship("Settlement", back_populates="merchant", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    payment_id = Column(String, primary_key=True, comment="Unique payment identifier")
    merchant_id = Column(String, ForeignKey("merchants.merchant_id"), nullable=False, comment="FK to merchants")
    order_id = Column(String, comment="Order reference ID")
    customer_id = Column(String, comment="Customer identifier")
    amount = Column(Numeric(12, 2), nullable=False, comment="Payment amount in INR")
    currency = Column(String, nullable=False, default="INR", comment="Currency code")
    status = Column(
        String,
        nullable=False,
        comment="Payment lifecycle status",
        default="created",
    )
    captured_at = Column(DateTime, comment="Timestamp when payment was captured")
    created_at = Column(DateTime, nullable=False, default=func.now())

    merchant = relationship("Merchant", back_populates="payments")
    refund = relationship("Refund", back_populates="payment", uselist=False)
    chargeback = relationship("Chargeback", back_populates="payment", uselist=False)


class Refund(Base):
    __tablename__ = "refunds"

    refund_id = Column(String, primary_key=True, comment="Unique refund identifier")
    payment_id = Column(String, ForeignKey("payments.payment_id"), comment="FK to payments")
    merchant_id = Column(
        String,
        ForeignKey("merchants.merchant_id"),
        comment="FK to merchants (denormalized for quick lookup)",
    )
    amount = Column(Numeric(12, 2), nullable=False, comment="Refund amount in INR")
    status = Column(
        String,
        nullable=False,
        comment="Refund processing status",
        default="pending",
    )
    processed_at = Column(DateTime, comment="Timestamp when refund was processed")
    created_at = Column(DateTime, nullable=False, default=func.now())

    payment = relationship("Payment", back_populates="refund", uselist=False)
    merchant = relationship("Merchant", back_populates="refunds")


class Settlement(Base):
    __tablename__ = "settlements"

    settlement_id = Column(String, primary_key=True, comment="Unique settlement identifier")
    merchant_id = Column(
        String,
        ForeignKey("merchants.merchant_id"),
        nullable=False,
        comment="FK to merchants",
    )
    amount = Column(Numeric(12, 2), nullable=False, comment="Settlement received amount in INR")
    window_start = Column(DateTime, comment="Settlement window start timestamp")
    cutoff_at = Column(
        DateTime, nullable=False, comment="Settlement processing cutoff time"
    )
    settled_at = Column(DateTime, comment="Timestamp when settlement was finalized")
    status = Column(
        String,
        nullable=False,
        comment="Settlement status",
        default="completed",
    )
    created_at = Column(DateTime, nullable=False, default=func.now())

    merchant = relationship("Merchant", back_populates="settlements")
    items = relationship("SettlementItem", back_populates="settlement", cascade="all, delete-orphan")
    fees = relationship("Fee", back_populates="settlement", cascade="all, delete-orphan")


class SettlementItem(Base):
    __tablename__ = "settlement_items"

    item_id = Column(String, primary_key=True, comment="Unique settlement item identifier")
    settlement_id = Column(
        String,
        ForeignKey("settlements.settlement_id"),
        nullable=False,
        comment="FK to settlements",
    )
    payment_id = Column(
        String,
        ForeignKey("payments.payment_id"),
        comment="FK to payments (may be NULL if deduction only)",
    )
    amount = Column(Numeric(12, 2), nullable=False, comment="Settlement item amount in INR")

    settlement = relationship("Settlement", back_populates="items")


class Fee(Base):
    __tablename__ = "fees"

    fee_id = Column(String, primary_key=True, comment="Unique fee identifier")
    merchant_id = Column(
        String,
        ForeignKey("merchants.merchant_id"),
        nullable=False,
        comment="FK to merchants",
    )
    settlement_id = Column(
        String,
        ForeignKey("settlements.settlement_id"),
        comment="FK to settlements (nullable for merchant-level fees)",
    )
    fee_type = Column(String, nullable=False, comment="Type of fee (platform, gateway, etc.)")
    amount = Column(Numeric(12, 2), nullable=False, comment="Fee amount in INR")
    created_at = Column(DateTime, nullable=False, default=func.now())

    merchant = relationship("Merchant", back_populates="fees")
    settlement = relationship("Settlement", back_populates="fees")


class Chargeback(Base):
    __tablename__ = "chargebacks"

    chargeback_id = Column(String, primary_key=True, comment="Unique chargeback identifier")
    payment_id = Column(
        String,
        ForeignKey("payments.payment_id"),
        comment="FK to payments",
    )
    amount = Column(Numeric(12, 2), nullable=False, comment="Chargeback amount in INR")
    status = Column(
        String,
        nullable=False,
        comment="Chargeback status",
        default="filed",
    )
    filed_at = Column(DateTime, comment="Timestamp when chargeback was filed")
    created_at = Column(DateTime, nullable=False, default=func.now())

    payment = relationship("Payment", back_populates="chargeback")


class FinancialEdge(Base):
    __tablename__ = "financial_edges"

    edge_id = Column(String, primary_key=True, comment="Unique edge identifier")
    from_type = Column(String, nullable=False, comment="Source entity type (payment, refund, etc.)")
    from_id = Column(String, nullable=False, comment="Source entity ID")
    to_type = Column(String, nullable=False, comment="Target entity type")
    to_id = Column(String, nullable=False, comment="Target entity ID")
    edge_type = Column(String, nullable=False, comment="Relationship type (caused_by, part_of, settled_in, etc.)")
    confidence = Column(Numeric(4, 2), nullable=False, default=1.0, comment="Confidence score 0.0-1.0")
    created_at = Column(DateTime, nullable=False, default=func.now())


class EventLog(Base):
    __tablename__ = "events"

    event_id = Column(String, primary_key=True, comment="Unique event identifier")
    event_type = Column(String, nullable=False, comment="Type of event (payment.captured, refund.processed, etc.)")
    payload = Column(Text, nullable=False, comment="JSON payload of event data")
    processed = Column(Boolean, nullable=False, default=False, comment="Whether event has been processed")
    created_at = Column(DateTime, nullable=False, default=func.now())
    processed_at = Column(DateTime, comment="Timestamp when event was processed")


class FinancialCase(Base):
    __tablename__ = "financial_cases"

    case_id = Column(String, primary_key=True, comment="Unique case identifier")
    object_type = Column(String, nullable=False, comment="Type of object (settlement, payment, refund)")
    object_id = Column(String, nullable=False, comment="ID of the referenced object")
    status = Column(
        String,
        nullable=False,
        comment="Case lifecycle status",
        default="NEW",
    )
    anomaly_type = Column(String, comment="Type of anomaly detected")
    root_cause = Column(Text, comment="Identified root cause description")
    root_cause_type = Column(String, comment="Structured root cause category")
    confidence = Column(Numeric(4, 2), comment="Confidence score 0.0-1.0")
    variance = Column(Numeric(12, 2), comment="Financial variance in INR")
    created_at = Column(DateTime, nullable=False, default=func.now())
    resolved_at = Column(DateTime, comment="Timestamp when case was resolved")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    evidence_id = Column(String, primary_key=True, comment="Unique evidence identifier")
    case_id = Column(
        String,
        ForeignKey("financial_cases.case_id"),
        comment="FK to financial cases",
    )
    claim = Column(Text, nullable=False, comment="What this evidence proves/supports")
    source_type = Column(
        String,
        nullable=False,
        comment="Evidence source type (database_record, calculation, policy_document, agent_inference)",
    )
    source_table = Column(String, comment="Database table name",
    )
    source_id = Column(String, comment="Record ID from source table")
    source_field = Column(String, comment="Field name within the source record")
    value = Column(Text, nullable=False, comment="The actual value from the record")
    agent_step_id = Column(String, comment="Which agent step produced this evidence")
    created_at = Column(DateTime, nullable=False, default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id = Column(String, primary_key=True, comment="Unique agent run identifier")
    case_id = Column(
        String,
        ForeignKey("financial_cases.case_id"),
        comment="FK to financial cases",
    )
    steps = Column(Text, nullable=False, comment="JSON array of agent step records")
    status = Column(
        String,
        comment="Agent run status",
        default="RUNNING",
    )
    started_at = Column(DateTime, nullable=False, default=func.now())
    completed_at = Column(DateTime, comment="Timestamp when agent run completed")


class AccountingEntry(Base):
    __tablename__ = "accounting_entries"

    entry_id = Column(String, primary_key=True, comment="Unique accounting entry identifier")
    entity_type = Column(String, comment="Type of entity (payment, settlement, chargeback)")
    entity_id = Column(String, comment="ID of the referenced entity")
    amount = Column(Numeric(12, 2), comment="Entry amount in INR")
    entry_type = Column(String, comment="Type of accounting entry (debit, credit, adjustment)")
    recorded_at = Column(DateTime, comment="Timestamp when entry was recorded")
    is_corrected = Column(Boolean, nullable=False, default=False, comment="Whether entry has been corrected")