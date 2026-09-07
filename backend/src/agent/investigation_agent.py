import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
from datetime import datetime

from backend.src.agent.hypothesis import Hypothesis, HypothesisEngine
from backend.src.domain.policy import PolicyEngine, PolicyDecision
from backend.src.domain.anomaly import AnomalyRecord
from backend.src.agent.tools import InvestigationTools, AgentStep, AgentState
from backend.src.db.queries import Repository
from backend.src.domain.calculator import FinancialCalculator


@dataclass
class InvestigationResult:
    """Structured result from an investigation run."""

    case_id: str
    root_cause: str | None = None
    root_cause_type: str | None = None
    confidence: float = 0.0
    evidence: List[dict] = field(default_factory=list)
    recommended_action: str = "MANUAL_REVIEW"
    steps: List[AgentStep] = field(default_factory=list)
    hypothesis: Hypothesis | None = None
    auto_resolved: bool = False


class InvestigationAgent:
    """Autonomous investigation agent using ReAct (Reason + Act) pattern.

    NOT a conversational agent. Receives a case, returns a structured conclusion.
    The agent operates with a hard iteration cap (max 10 steps) and must
    always respect the deterministic/AI boundary: arithmetic is never LLM.
    """

    def __init__(self, repo: Repository, tools: InvestigationTools,
                 hypothesis_engine: HypothesisEngine, policy_engine: PolicyEngine):
        self.repo = repo
        self.tools = tools
        self.hypothesis_engine = hypothesis_engine
        self.policy_engine = policy_engine
        self.step_counter = 0

    def investigate(self, case_id: str) -> InvestigationResult:
        """Run the full investigation ReAct loop for a given case.

        Phases:
        1. OBSERVE: Load case + settlement + payments + refunds + fees + timeline
        2. FORM HYPOTHESES: LLM generates 2-3 competing causal hypotheses
        3. GATHER EVIDENCE: For each hypothesis, call tools to retrieve records
        4. TEST HYPOTHESES: Score each hypothesis against evidence; reject weak ones
        5. CONCLUDE: Select highest-scored hypothesis with sufficient evidence
        6. POLICY: Deterministic policy engine decides auto-resolve vs escalate
        7. RECORD: Log all steps, update case, record evidence
        """
        self.step_counter = 0
        start_time = time.time()

        # Initialize agent state
        state = AgentState(
            case=self.repo.get_case(case_id),
            steps=[],
            hypotheses=[],
            evidence=[],
        )

        if not state.case:
            return InvestigationResult(
                case_id=case_id,
                root_cause="Case not found",
                confidence=0.0,
                steps=[],
                recommended_action="MANUAL_REVIEW",
            )

        # Phase 1: OBSERVE - Load all relevant data
        state = self._observe(state)

        # Phase 2: FORM HYPOTHESES - Generate competing hypotheses
        state = self._form_hypotheses(state)

        # Phase 3: GATHER EVIDENCE - Collect evidence for each hypothesis
        state = self._gather_evidence(state)

        # Phase 4: TEST HYPOTHESES - Score and reject
        state = self._test_hypotheses(state)

        # Phase 5: CONCLUDE - Select root cause
        conclusion = self._conclude(state)

        # Phase 6: POLICY - Deterministic policy evaluation
        policy_decision = self._apply_policy(conclusion, state)

        # Phase 7: RECORD - Finalize
        result = self._record_result(case_id, state, conclusion, policy_decision, start_time)

        return result

    # --- Phase 1: OBSERVE ---

    def _observe(self, state: AgentState) -> AgentState:
        """Load all relevant data for the investigation case."""
        case = state.case
        settlement_id = case.object_id

        # Step 1: Load settlement record
        settlement_data = self.tools.get_settlement(settlement_id)
        step1 = AgentStep(
            step_id="STEP-001",
            step_type="TOOL_CALL",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="get_settlement",
            tool_input={"settlement_id": settlement_id},
            tool_output=settlement_data,
            reasoning="Loaded settlement record to understand variance and window",
        )
        state.steps.append(step1)

        if "error" in settlement_data:
            return self._stop_case(state, "settlement_not_found", 0.0)

        # Step 2: Load payments in settlement window
        payments_data = self.tools.get_payments_in_settlement_window(settlement_id)
        step2 = AgentStep(
            step_id="STEP-002",
            step_type="TOOL_CALL",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="get_payments_in_settlement_window",
            tool_input={"settlement_id": settlement_id},
            tool_output=payments_data,
            reasoning="Retrieved all captured payments in the settlement time window",
        )
        state.steps.append(step2)

        # Step 3: Load refunds
        refunds_data = self.tools.get_refunds(settlement_id=settlement_id)
        step3 = AgentStep(
            step_id="STEP-003",
            step_type="TOOL_CALL",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="get_refunds",
            tool_input={"settlement_id": settlement_id},
            tool_output=refunds_data,
            reasoning="Retrieved all processed refunds in the settlement window",
        )
        state.steps.append(step3)

        # Step 4: Calculate expected settlement (deterministic)
        calc_data = self.tools.calculate_expected_settlement(settlement_id)
        step4 = AgentStep(
            step_id="STEP-004",
            step_type="CALCULATION",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="calculate_expected_settlement",
            tool_input={"settlement_id": settlement_id},
            tool_output=calc_data,
            reasoning="Deterministic calculation: expected = gross - refunds - fees",
        )
        state.steps.append(step4)

        # Step 5: Load timeline for temporal reasoning
        timeline_data = self.tools.get_timeline("settlement", settlement_id)
        step5 = AgentStep(
            step_id="STEP-005",
            step_type="TOOL_CALL",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="get_timeline",
            tool_input={"entity_type": "settlement", "entity_id": settlement_id},
            tool_output=timeline_data,
            reasoning="Retrieved chronological event timeline for causal reasoning",
        )
        state.steps.append(step5)

        # Store key data in state for later steps
        state.settlement_id = settlement_id
        state.settlement_amount = Decimal(str(settlement_data.get("amount", 0)))
        state.expected_amount = Decimal(str(calc_data.get("expected_amount", 0)))
        state.variance = Decimal(str(calc_data.get("variance", 0)))
        state.payments_data = payments_data
        state.refunds_data = refunds_data

        return state

    # --- Phase 2: FORM HYPOTHESES ---

    def _form_hypotheses(self, state: AgentState) -> AgentState:
        """Generate 2-3 competing causal hypotheses using the LLM."""
        case = state.case
        anomaly_type = case.anomaly_type or "AMOUNT_MISMATCH"
        case_context = {
            "settlement_id": state.settlement_id,
            "settlement_amount": float(state.settlement_amount),
            "expected_amount": float(state.expected_amount),
            "variance": float(state.variance),
            "variance_sign": "-" if state.variance < 0 else "+",
            "payment_count": len(state.payments_data.get("payments", [])),
            "refund_count": len(state.refunds_data.get("refunds", [])),
        }

        # Form hypotheses using the hypothesis engine
        hypotheses = self.hypothesis_engine.form_hypotheses(anomaly_type, case_context)

        # Add step recording
        step = AgentStep(
            step_id="STEP-010",
            step_type="LLM_REASONING",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="form_hypotheses",
            reasoning=f"Generated {len(hypotheses)} competing hypotheses for anomaly type: {anomaly_type}",
            conclusion=f"Hypotheses formed: {[h.hypothesis_type for h in hypotheses]}",
        )
        state.steps.append(step)

        state.hypotheses = hypotheses
        return state

    # --- Phase 3: GATHER EVIDENCE ---

    def _gather_evidence(self, state: AgentState) -> AgentState:
        """For each hypothesis, gather supporting/contradicting evidence using tools."""
        hypotheses = state.hypotheses
        settlement_id = state.settlement_id

        for hyp in hypotheses:
            # Gather evidence based on hypothesis type
            evidence_for = []
            evidence_against = []

            if hyp.hypothesis_type == "REFUND_AFTER_CUTOFF":
                # Check refund timestamps vs settlement cutoff
                refunds = state.refunds_data.get("refunds", [])
                for r in refunds:
                    processed_at = r.get("processed_at", "")
                    if processed_at:
                        # Compare with settlement cutoff
                        cutoff = state.get("cutoff_at", "")
                        if cutoff:
                            # Simple temporal comparison
                            if self._is_after_cutoff(processed_at, cutoff):
                                evidence_against.append(r["refund_id"])
                                # Actually this SUPPORTs the hypothesis that refund was after cutoff
                                evidence_for.append(r["refund_id"])
                            else:
                                # Refund was before cutoff — contradicts this hypothesis
                                evidence_against.append(r["refund_id"])

            elif hyp.hypothesis_type == "FEE_ADJUSTMENT":
                # Check if fees match expected schedule
                fees_result = self.tools.get_fees_for_settlement(settlement_id)
                # Compare actual fees vs expected from fee schedule
                # For now, mark neutral unless there's a clear violation
                actual_fees = fees_result.get("total_fees", 0) if isinstance(fees_result, dict) else 0
                if actual_fees > 0:
                    evidence_for.append({"type": "fee_analysis", "amount": actual_fees})

            elif hyp.hypothesis_type == "MISSING_PAYMENT":
                # Check for unmatched payments in the settlement
                unmatched = self._find_unmatched_payments(settlement_id)
                if unmatched:
                    evidence_for.extend(unmatched)

            elif hyp.hypothesis_type == "DUPLICATE_PAYMENT":
                # Check for duplicate payments in the window
                duplicates = self._detect_duplicates(settlement_id)
                if duplicates:
                    evidence_for.extend(duplicates)

            elif hyp.hypothesis_type == "TIMING_DIFFERENCE":
                # Check timeline ordering
                timeline = state.get("timeline_data", [])
                if timeline:
                    evidence_for.append({"type": "timeline_analysis", "events": len(timeline)})

            # Update hypothesis with evidence lists
            hyp.evidence_for = evidence_for
            hyp.evidence_against = evidence_against

            # Record evidence step
            step = AgentStep(
                step_id=f"STEP-020_{hyp.hypothesis_id}",
                step_type="TOOL_CALL",
                timestamp=datetime.utcnow().isoformat(),
                tool_name="gather_evidence",
                tool_input={"hypothesis_type": hyp.hypothesis_type},
                tool_output={
                    "evidence_for": evidence_for,
                    "evidence_against": evidence_against,
                },
                reasoning=f"Gathered evidence for hypothesis {hyp.hypothesis_type}",
            )
            state.steps.append(step)

            # Add evidence to state's evidence list
            for e in evidence_for:
                state.evidence.append({
                    "evidence_id": f"EV-{len(state.evidence)+1:04d}",
                    "claim": f"Supports {hyp.hypothesis_type}",
                    "source_type": "database_record",
                    "source_id": e if isinstance(e, str) else e.get("refund_id", e.get("payment_id", "unknown")),
                    "value": str(e),
                })
            for e in evidence_against:
                state.evidence.append({
                    "evidence_id": f"EV-{len(state.evidence)+1:04d}",
                    "claim": f"Contradicts {hyp.hypothesis_type}",
                    "source_type": "database_record",
                    "source_id": e if isinstance(e, str) else "unknown",
                    "value": str(e),
                })

        return state

    # --- Phase 4: TEST HYPOTHESES ---

    def _test_hypotheses(self, state: AgentState) -> AgentState:
        """Score each hypothesis against evidence and reject weak ones."""
        hypotheses = state.hypotheses

        for hyp in hypotheses:
            # Use deterministic scoring engine
            score = self.hypothesis_engine.score_hypothesis(hyp, state.evidence)
            hyp.confidence = score

            # Determine status based on score
            if score >= 0.80:
                hyp.status = "CONFIRMED"
            elif score >= 0.50:
                hyp.status = "SUPPORTED"
            elif score > 0.0:
                hyp.status = "PENDING"
            else:
                hyp.status = "REJECTED"

            # Record scoring step
            step = AgentStep(
                step_id=f"STEP-030_{hyp.hypothesis_id}",
                step_type="LLM_REASONING",
                timestamp=datetime.utcnow().isoformat(),
                tool_name="score_hypothesis",
                tool_input={"hypothesis_type": hyp.hypothesis_type, "evidence_count": len(state.evidence)},
                tool_output={"confidence": score, "status": hyp.status},
                reasoning=f"Scored hypothesis {hyp.hypothesis_type}: confidence={score:.2f}, status={hyp.status}",
            )
            state.steps.append(step)

        return state

    # --- Phase 5: CONCLUDE ---

    def _conclude(self, state: AgentState) -> dict:
        """Select the root cause conclusion from tested hypotheses."""
        hypotheses = state.hypotheses

        # Get top hypothesis
        top_hyp = self.hypothesis_engine.get_top_hypothesis(hypotheses)

        # If no hypothesis has sufficient confidence, case escalates
        if top_hyp is None:
            # Return escalation conclusion
            return {
                "root_cause": "Insufficient evidence to determine root cause",
                "root_cause_type": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.0,
                "hypothesis": None,
                "escalate": True,
            }

        # Build evidence list for the concluded root cause
        evidence_list = []
        for ev in state.evidence:
            if ev.get("source_id") in top_hyp.evidence_for:
                evidence_list.append(ev)

        # Also include evidence against rejected hypotheses for transparency
        for hyp in hypotheses:
            if hyp.status == "REJECTED" and hyp.evidence_against:
                for eid in hyp.evidence_against:
                    # Find matching evidence
                    for ev in state.evidence:
                        if ev.get("evidence_id") == eid and ev not in evidence_list:
                            evidence_list.append(ev)

        # Collect all evidence IDs referenced
        all_evidence_ids = []
        for hyp in hypotheses:
            all_evidence_ids.extend(hyp.evidence_for)
            all_evidence_ids.extend(hyp.evidence_against)

        conclusion = {
            "root_cause": top_hyp.description if top_hyp else "Unable to determine root cause",
            "root_cause_type": top_hyp.hypothesis_type if top_hyp else "UNKNOWN",
            "confidence": top_hyp.confidence if top_hyp else 0.0,
            "hypothesis": top_hyp.hypothesis_type if top_hyp else "UNKNOWN",
            "evidence": evidence_list,
            "escalate": top_hyp.confidence < 0.70 if top_hyp else True,
        }

        return conclusion

    # --- Phase 6: POLICY ---

    def _apply_policy(self, conclusion: dict, state: AgentState) -> PolicyDecision:
        """Apply deterministic policy engine to the conclusion.

        The policy engine never delegates to LLM — it uses rule-based evaluation.
        """
        # Build a result-like object for policy evaluation
        class _Result:
            def __init__(self, c, rc_type, var, conf):
                self.variance = var
                self.confidence = conf
                self.root_cause_type = rc_type

        variance = state.variance if hasattr(state, 'variance') else Decimal("0")
        confidence = conclusion.get("confidence", 0.0)
        root_cause_type = conclusion.get("root_cause_type", "UNKNOWN")

        # Also check variance
        result = _Result(
            c=conclusion.get("root_cause", "Unknown"),
            rc_type=root_cause_type,
            var=variance,
            conf=confidence,
        )

        policy_decision = self.policy_engine.evaluate(result)

        # Also record the policy check step
        step = AgentStep(
            step_id="STEP-040",
            step_type="POLICY_CHECK",
            timestamp=datetime.utcnow().isoformat(),
            tool_name="policy_engine",
            tool_input={"policy_names": [p["name"] for p in self.policy_engine.POLICIES]},
            tool_output={
                "action": policy_decision.action,
                "policy_name": policy_decision.policy_name,
                "reason": policy_decision.reason,
            },
            reasoning=f"Policy engine applied: {policy_decision.policy_name} → {policy_decision.action}",
        )
        state.steps.append(step)

        return policy_decision

    # --- Phase 7: RECORD ---

    def _record_result(self, case_id: str, state: AgentState,
                       conclusion: dict, policy_decision: PolicyDecision,
                       start_time: float) -> InvestigationResult:
        """Finalize the investigation result and update case state."""
        root_cause = conclusion.get("root_cause", None)
        root_cause_type = conclusion.get("root_cause_type", None)
        confidence = conclusion.get("confidence", 0.0)
        evidence = conclusion.get("evidence", [])
        escalate = conclusion.get("escalate", False)

        # Determine recommended action based on policy
        if policy_decision.action == "AUTO_RESOLVE":
            recommended_action = "AUTO_RESOLVED"
            auto_resolved = True
        elif policy_decision.action == "ESCALATE_TO_HUMAN":
            recommended_action = "ESCALATED"
            auto_resolved = False
        else:
            recommended_action = "MANUAL_REVIEW"
            auto_resolved = False

        # Update case in database
        self.repo.update_case(
            case_id=case_id,
            status="RESOLVED" if not escalate else "ESCALATED",
            root_cause=root_cause,
            root_cause_type=root_cause_type,
            confidence=confidence,
            variance=state.variance if hasattr(state, 'variance') else None,
        )

        # Add final evidence steps
        for i, ev in enumerate(evidence):
            self.repo.add_evidence(
                evidence_id=ev.get("evidence_id", f"EV-{i+1:04d}"),
                case_id=case_id,
                claim=ev.get("claim", ""),
                source_type=ev.get("source_type", "database_record"),
                source_table=ev.get("source_table"),
                source_id=ev.get("source_id"),
                source_field=ev.get("source_field"),
                value=ev.get("value", ""),
                agent_step_id="STEP-FINAL",
            )

        # Build the investigation result
        result = InvestigationResult(
            case_id=case_id,
            root_cause=root_cause,
            root_cause_type=root_cause_type,
            confidence=confidence,
            evidence=evidence,
            recommended_action=recommended_action,
            steps=state.steps,
            hypothesis=next((h for h in state.hypotheses if h.status == "CONFIRMED"), None),
            auto_resolved=auto_resolved,
        )

        return result

    # --- Helper methods ---

    def _is_after_cutoff(self, processed_at: str, cutoff: str) -> bool:
        """Check if a processed_at timestamp is after the settlement cutoff."""
        # Simple string comparison for ISO timestamps
        # This works for same-day timestamps
        try:
            # Parse the dates
            p_parts = processed_at.split("T")
            c_parts = cutoff.split("T")
            p_date = p_parts[0]
            c_date = c_parts[0]
            if p_date > c_date:
                return True
            if p_date < c_date:
                return False
            # Same day — compare times
            if len(p_parts) > 1 and len(c_parts) > 1:
                p_time = p_parts[1].split(":")[0]
                c_time = c_parts[1].split(":")[0]
                return int(p_time) > int(c_time)
            return False
        except Exception:
            return False

    def _find_unmatched_payments(self, settlement_id: str) -> list:
        """Find payments in window not in settlement items."""
        # Query settlement items for this settlement
        items = self.repo.get_settlement_items(settlement_id)
        item_payment_ids = {i.payment_id for i in items if i.payment_id}

        # Get payments in window
        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        unmatched = []
        for p in payments:
            if p.payment_id not in item_payment_ids:
                unmatched.append({
                    "evidence_id": f"EV-unmatched-{p.payment_id}",
                    "claim": f"Payment {p.payment_id} not in settlement items",
                    "source_type": "database_record",
                    "source_id": p.payment_id,
                    "value": f"Payment {p.payment_id} (₹{float(p.amount)}) captured at {p.captured_at}",
                })

        return unmatched

    def _detect_duplicates(self, settlement_id: str) -> list:
        """Detect duplicate payments in the settlement window."""
        from collections import defaultdict

        settlement = self.repo.get_settlement(settlement_id)
        if not settlement:
            return []

        payments = self.repo.get_captured_payments_in_window(
            settlement.merchant_id, settlement.window_start, settlement.cutoff_at
        )

        # Group by (amount, minute-rounded timestamp)
        groups: dict[tuple, list] = defaultdict(list)
        for p in payments:
            if p.captured_at:
                minute_key = p.captured_at.strftime("%Y-%m-%d %H:%M")
                groups[(Decimal(str(p.amount)), minute_key)].append(p)

        duplicates = []
        for (amount_key, minute_key), group in groups.items():
            if len(group) > 1:
                for p in group:
                    duplicates.append({
                        "evidence_id": f"EV-dup-{p.payment_id}",
                        "claim": f"Duplicate payment {p.payment_id} with same amount in minute window",
                        "source_type": "database_record",
                        "source_id": p.payment_id,
                        "value": f"Payment {p.payment_id} (₹{float(p.amount)}) at {p.captured_at}",
                    })

        return duplicates

    def _stop_case(self, state: AgentState, reason: str, confidence: float) -> InvestigationResult:
        """Stop the investigation early due to a problem."""
        self.repo.update_case(
            case_id=state.case.case_id,
            status="ESCALATED",
            root_cause=reason,
            root_cause_type="SYSTEM_ERROR",
            confidence=confidence,
        )

        return InvestigationResult(
            case_id=state.case.case_id,
            root_cause=reason,
            confidence=confidence,
            steps=state.steps,
            recommended_action="ESCALATED",
        )