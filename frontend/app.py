import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime
from decimal import Decimal
import json

# Page config
st.set_page_config(
    page_title="LedgerLens — AI Finance Controller",
    page_icon="💰",
    layout="wide",
)

st.title("💰 LedgerLens — AI Finance Controller")
st.caption("Autonomous causal investigation of payment anomalies")

# Session state initialization
if "repo_initialized" not in st.session_state:
    st.session_state.repo_initialized = False
if "dataset_loaded" not in st.session_state:
    st.session_state.dataset_loaded = False
if "cases" not in st.session_state:
    st.session_state.cases = []
if "current_case" not in st.session_state:
    st.session_state.current_case = None
if "benchmark_results" not in st.session_state:
    st.session_state.benchmark_results = None

# --- Sidebar ---
st.sidebar.title("Navigation")
view = st.sidebar.radio(
    "Select View",
    ["Batch Summary", "Investigation Workspace", "Agent Trace", "Human Review Queue"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("### Dataset")
if st.sidebar.button("Load Sample Dataset"):
    # Trigger data generation
    import subprocess
    result = subprocess.run(
        ["/home/ujwal-mahajan/.pyenv/versions/3.12.11/bin/python", "-m", "ledgerlens.dataset.generator", "--seed", "42", "--payments", "200", "--anomalies", "15"],
        capture_output=True, text=True, cwd="/home/ujwal-mahajan/Desktop/Rzorpay/ledgerlens"
    )
    st.sidebar.info(result.stdout[-200:] if len(result.stdout) > 200 else result.stdout)
    if result.returncode == 0:
        st.session_state.dataset_loaded = True
        st.sidebar.success("Dataset loaded successfully!")
    else:
        st.sidebar.error(result.stderr[-300:] if len(result.stderr) > 300 else result.stderr)

st.sidebar.markdown("### API")
st.sidebar.info("API Key: " + ("*" * 10))

# --- View A: Batch Summary Dashboard ---
if view == "Batch Summary":
    st.header("Batch Summary Dashboard")
    
    if not st.session_state.dataset_loaded:
        st.info("Load a sample dataset to begin.")
        st.stop()
    
    # Get settlements from the repo
    # In a real implementation, we'd query the DB
    # For now, show placeholder info
    st.markdown("""
    **Batch Summary Overview**
    
    This dashboard shows the reconciliation results for the loaded batch of synthetic settlements.
    
    **Metrics:**
    - Total Settlements: 50+
    - Match Rate: --%
    - Minor Discrepancies: --
    - Major Discrepancies: --
    - Auto-Resolved: --
    - Escalated: --
    
    **Anomaly Type Distribution:**
    - REFUND_AFTER_CUTOFF: --
    - MISSING_SETTLEMENT_ITEM: --
    - DUPLICATE_PAYMENT: --
    - FEE_SCHEDULE_VIOLATION: --
    - AMOUNT_DISCREPANCY: --
    
    Use the Investigation Workspace to examine individual settlements.
    """)
    
    # Placeholder for actual data
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Settlements", "52")
    with col2:
        st.metric("Match Rate", "88%")
    with col3:
        st.metric("Minor Disc.", "8")
    with col4:
        st.metric("Escalated", "3")

# --- View B: Investigation Workspace ---
elif view == "Investigation Workspace":
    st.header("Investigation Workspace")
    
    if not st.session_state.dataset_loaded:
        st.info("Load a sample dataset to begin investigation.")
        st.stop()
    
    # Settlement selector
    # In a real implementation, we'd query the DB for settlements
    settlement_options = ["S-2024-0001", "S-2024-0002", "S-2024-0003", "S-2024-0004", "S-2024-0005"]
    
    if "selected_settlement" not in st.session_state:
        st.session_state.selected_settlement = settlement_options[0]
    
    selected = st.selectbox(
        "Select Settlement to Investigate",
        settlement_options,
        index=settlement_options.index(st.session_state.selected_settlement)
        if st.session_state.selected_settlement in settlement_options else 0
    )
    
    st.session_state.selected_settlement = selected
    
    if selected:
        st.markdown(f"### Settlement: {selected}")
        
        # Settlement detail columns
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Amount Received", "₹1,84,320")
            st.metric("Expected Amount", "₹1,86,500")
        with col2:
            st.metric("Variance", "-₹2,180")
            st.metric("Variance %", "-1.17%")
        
        # Causal chain visualization
        st.markdown("#### Causal Chain")
        st.markdown("""
        **Payment → Refund → Settlement → Fee → Accounting Entry**
        
        The agent traverses this chain to reconstruct the causal sequence.
        """)
        
        # Hypotheses panel
        st.markdown("#### Competing Hypotheses")
        
        hypotheses = [
            {
                "type": "REFUND_AFTER_CUTOFF",
                "description": "Refund was processed after the settlement cutoff time",
                "status": "CONFIRMED",
                "confidence": 0.94,
                "evidence_count": 3,
            },
            {
                "type": "FEE_ADJUSTMENT",
                "description": "Fee amount differs from expected fee schedule",
                "status": "REJECTED",
                "confidence": 0.12,
                "evidence_count": 0,
            },
            {
                "type": "TIMING_DIFFERENCE",
                "description": "Transaction timestamps across settlement cycle boundaries",
                "status": "REJECTED",
                "confidence": 0.08,
                "evidence_count": 0,
            },
        ]
        
        for hyp in hypotheses:
            status_color = "🟢" if hyp["status"] == "CONFIRMED" else "🔴" if hyp["status"] == "REJECTED" else "🟡"
            with st.expander(f"{status_color} {hyp['type']} — Confidence: {hyp['confidence']:.2f}"):
                st.write(hyp["description"])
                st.caption(f"Evidence: {hyp['evidence_count']} items")
        
        # Evidence list
        st.markdown("#### Evidence")
        evidence_items = [
            {"claim": "Refund R091 processed at 00:03:12 UTC", "source": "database_record", "source_id": "R091"},
            {"claim": "Settlement cutoff: 00:00:00 UTC (policy doc §3.2)", "source": "policy_document", "source_id": "policy_3.2"},
            {"claim": "Arithmetic: ₹1,86,500 - ₹2,180 = ₹1,84,320 ✓ EXACT MATCH", "source_type": "calculation"},
        ]
        
        for i, ev in enumerate(evidence_items):
            with st.expander(f"Evidence {i+1}"):
                st.write(f"**Claim:** {ev['claim']}")
                st.write(f"**Source Type:** {ev['source']}")
                if ev.get('source_id'):
                    st.write(f"**Source ID:** {ev['source_id']}")
        
        # Root cause conclusion
        st.markdown("#### Root Cause Conclusion")
        st.markdown("""
        **Root Cause:** Refund R091 was processed 3 minutes after settlement cutoff.
        **Confidence:** 0.94
        
        **Impact:** ₹2,180 will appear as deduction in next settlement cycle.
        
        **Recommended Action:** 
        - Policy: Variance ₹2,180 ≥ ₹500 → ESCALATE TO HUMAN
        - Manual review required before case closure
        """)
        
        # Policy decision
        st.markdown("#### Policy Decision")
        st.markdown("""
        **Action:** ESCALATE TO HUMAN
        **Reason:** Variance ₹2,180 exceeds auto-resolution threshold of ₹500
        **Policy:** Major discrepancy always escalates
        """)

# --- View C: Agent Trace View ---
elif view == "Agent Trace":
    st.header("Agent Trace — Step-by-Step Investigation")
    
    if not st.session_state.dataset_loaded:
        st.info("Load a sample dataset to see the agent trace.")
        st.stop()
    
    st.markdown("""
    The agent trace shows every step the investigation agent took, in chronological order.
    Each step is a ReAct loop iteration: the agent observed, formed hypotheses, 
    gathered evidence, tested them, and concluded.
    """)
    
    # Sample trace data (from the PRD Section 28)
    trace_steps = [
        ("09:14:30.001", "[CASE OPENED]", "Case C-0391 | Settlement S-0847 | Variance ₹-2,180"),
        ("09:14:30.012", "[TOOL CALL]", "get_settlement(\"S-0847\") → amount: ₹1,84,320, cutoff: 2024-01-16 00:00"),
        ("09:14:30.045", "[TOOL CALL]", "get_payments_in_settlement_window(\"S-0847\") → 47 payments, total ₹1,86,500"),
        ("09:14:30.089", "[TOOL CALL]", "get_refunds(settlement_id=\"S-0847\") → 3 refunds: R089 ₹800, R090 ₹1,200, R091 ₹2,180"),
        ("09:14:30.102", "[CALCULATION]", "expected = ₹1,86,500 - ₹4,180 (refunds) - ₹0 (fees) = ₹1,82,320 ← MISMATCH"),
        ("09:14:30.115", "[LLM REASONING]", "\"Variance suggests one refund may be excluded from settlement. Forming 3 hypotheses.\""),
        ("09:14:30.201", "[HYPOTHESIS]", "H1: \"Refund processed after cutoff\" — PENDING"),
        ("09:14:30.202", "[HYPOTHESIS]", "H2: \"Fee adjustment not in records\" — PENDING"),
        ("09:14:30.203", "[HYPOTHESIS]", "H3: \"Payment amount difference\" — PENDING"),
        ("09:14:30.301", "[TOOL CALL]", "get_timeline(\"refund\", \"R091\") → processed_at: 2024-01-16T00:03:12Z"),
        ("09:14:30.315", "[EVIDENCE]", "EV-001: R091 processed_at 00:03:12 > cutoff 00:00:00 → SUPPORTS H1"),
        ("09:14:30.401", "[TOOL CALL]", "get_fees(\"S-0847\") → 0 fees applied"),
        ("09:14:30.412", "[EVIDENCE]", "EV-002: No fees → REJECTS H2"),
        ("09:14:30.501", "[CALCULATION]", "With R091 excluded: ₹1,86,500 - (₹800+₹1,200) = ₹1,84,500 ← still off by ₹180"),
        ("09:14:30.601", "[TOOL CALL]", "get_settlement_items(\"S-0847\") → R091 NOT in settlement deductions ✓"),
        ("09:14:30.611", "[EVIDENCE]", "EV-003: Settlement deductions match ₹2,000 (R089+R090) only → CONFIRMS H1"),
        ("09:14:30.701", "[HYPOTHESIS]", "H1: CONFIRMED (confidence: 0.94)  H2: REJECTED  H3: REJECTED"),
        ("09:14:30.801", "[POLICY CHECK]", "Variance ₹2,180 ≥ ₹500 → ESCALATE_TO_HUMAN"),
        ("09:14:30.802", "[CASE UPDATED]", "Status: HYPOTHESIS_FORMED → AWAITING_APPROVAL"),
        ("09:14:30.803", "[COMPLETE]", "Investigation complete. Root cause: REFUND_AFTER_CUTOFF. Confidence: 0.94"),
    ]
    
    # Display trace
    for timestamp, step_type, description in trace_steps:
        # Color code by step type
        if "[CASE OPENED]" in description:
            st.success(f"{timestamp}  {step_type}  {description}")
        elif "[TOOL CALL]" in description:
            st.info(f"{timestamp}  {step_type}  {description}")
        elif "[LLM REASONING]" in description or "[HYPOTHESIS]" in description:
            st.warning(f"{timestamp}  {step_type}  {description}")
        elif "[CALCULATION]" in description:
            st.text(f"{timestamp}  {step_type}  {description}")
        elif "[EVIDENCE]" in description:
            st.text(f"{timestamp}  {step_type}  {description}")
        elif "[POLICY CHECK]" in description:
            st.error(f"{timestamp}  {step_type}  {description}")
        elif "[CASE UPDATED]" in description:
            st.text(f"{timestamp}  {step_type}  {description}")
        elif "[COMPLETE]" in description:
            st.beta_success(f"{timestamp}  {step_type}  {description}")
        else:
            st.text(f"{timestamp}  {step_type}  {description}")

# --- View D: Human Review Queue ---
elif view == "Human Review Queue":
    st.header("Human Review Queue — Escalated Cases")
    
    if not st.session_state.dataset_loaded:
        st.info("Load a sample dataset to see escalated cases.")
        st.stop()
    
    st.markdown("""
    Cases that require human review appear here. These are typically:
    - Discrepancies ≥ ₹500 (auto-resolve threshold exceeded)
    - Low-confidence investigations (confidence < 0.70)
    - Duplicate payments (always escalated)
    - Cases with contradicting evidence
    """)
    
    # Sample escalated cases
    escalated_cases = [
        {
            "case_id": "C-0391",
            "settlement": "S-0847",
            "variance": "-₹2,180",
            "root_cause": "Refund R091 processed after cutoff",
            "confidence": 0.94,
            "status": "AWAITING_APPROVAL",
            "actions": ["Mark Resolved", "Reject", "Add Note"],
        },
        {
            "case_id": "C-0392",
            "settlement": "S-0848",
            "variance": "-₹850",
            "root_cause": "Insufficient evidence — multiple plausible hypotheses",
            "confidence": 0.52,
            "status": "ESCALATED",
            "actions": ["Mark Resolved", "Reject", "Request More Data"],
        },
        {
            "case_id": "C-0395",
            "settlement": "S-0850",
            "variance": "₹1,250",
            "root_cause": "Duplicate payments within 60-second window",
            "confidence": 0.89,
            "status": "AWAITING_APPROVAL",
            "actions": ["Mark Resolved", "Reject", "Add Note"],
        },
    ]
    
    for case in escalated_cases:
        with st.expander(f"Case {case['case_id']} — {case['settlement']}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Variance:** {case['variance']}")
                st.write(f"**Root Cause:** {case['root_cause']}")
                st.write(f"**Confidence:** {case['confidence']:.2f}")
                st.write(f"**Status:** {case['status']}")
            with col2:
                st.write(f"**Settlement:** {case['settlement']}")
                st.write(f"**Actions:** {', '.join(case['actions'])}")
                if st.button(f"Mark Resolved — {case['case_id']}", key=f"resolve_{case['case_id']}"):
                    st.success(f"Case {case['case_id']} marked as resolved")
                if st.button(f"Reject — {case['case_id']}", key=f"reject_{case['case_id']}"):
                    st.info(f"Case {case['case_id']} rejected for review")

# Run the app instruction
st.sidebar.markdown("---")
st.sidebar.markdown("### Developer Notes")
st.sidebar.caption("""
For the full demo:
1. Load the sample dataset
2. Go to 'Batch Summary' to see overall stats
3. Investigate individual settlements in 'Investigation Workspace'
4. Review the agent trace in 'Agent Trace' view
5. Check 'Human Review Queue' for escalated cases
6. Run benchmark via the API endpoint
""")