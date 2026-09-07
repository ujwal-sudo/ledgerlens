from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import os
import logging

from ...db.database import init_db, get_session
from ...db.models import FinancialCase
from ...domain.calculator import FinancialCalculator
from ...domain.policy import PolicyEngine
from ...agent.investigation_agent import InvestigationAgent
from ...agent.tools import InvestigationTools
from ...agent.hypothesis import HypothesisEngine
from ...db.queries import Repository

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API key dependency
API_KEY_NAME = "LEDGERLENS_API_KEY"


def get_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """Validate API key for service-to-service auth."""
    expected_key = os.environ.get("LEDGERLENS_API_KEY", "dev_key_12345")
    if x_api_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return x_api_key


# Create FastAPI app
app = FastAPI(
    title="LedgerLens API",
    description="AI Financial Investigator - Reconciliation and causal investigation of settlement discrepancies",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global state
repo: Repository | None = None
tools: InvestigationTools | None = None
agent: InvestigationAgent | None = None


# --- Pydantic models ---


class IngestCSV(BaseModel):
    """Request body for CSV ingestion."""
    csv_data: str = Field(description="CSV settlement report data")
    dataset_name: Optional[str] = Field(default=None, description="Name of the dataset seed")


class ReconcileBatchRequest(BaseModel):
    """Request body for batch reconciliation."""
    dataset: str = Field(description="Dataset seed name to reconcile")


class CaseResponse(BaseModel):
    """Response model for case details."""
    case_id: str
    object_type: str
    object_id: str
    status: str
    anomaly_type: Optional[str] = None
    root_cause: Optional[str] = None
    root_cause_type: Optional[str] = None
    confidence: Optional[float] = None
    variance: Optional[float] = None
    evidence: list[dict] = Field(default_factory=list)
    agent_trace: list[dict] = Field(default_factory=list)
    created_at: str
    resolved_at: Optional[str] = None


# --- API Routes ---


@app.on_event("startup")
async def startup_event():
    """Initialize database and default components on startup."""
    global repo, tools, agent

    db_path = os.environ.get("DB_PATH", "./ledgerlens.db")

    # Initialize database
    engine = init_db(db_path)

    # Create repository
    session = get_session(db_path)
    repo = Repository(session)

    # Initialize domain components
    calculator = FinancialCalculator()
    policy_engine = PolicyEngine()

    # Initialize hypothesis engine
    hypothesis_engine = HypothesisEngine()

    # Initialize tools
    tools = InvestigationTools(repo, calculator, policy_engine)

    # Initialize investigation agent
    agent = InvestigationAgent(repo, tools, hypothesis_engine, policy_engine)

    logger.info("LedgerLens API initialized successfully")


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "ledgerlens"}


@app.post("/api/ingest/csv")
async def ingest_csv(
    request: IngestCSV,
    api_key: str = Depends(get_api_key),
):
    """Ingest synthetic financial data from CSV/JSON.

    Loads a previously generated dataset into the database.
    """
    global repo, tools, agent

    if not repo:
        raise HTTPException(status_code=500, detail="API not initialized")

    # For now, we generate synthetic data on-the-fly
    # In a full implementation, this would parse the CSV and insert records

    # Generate dataset with known ground truth
    from dataset.generator import SyntheticDataGenerator
    gen = SyntheticDataGenerator(seed=42)
    dataset = gen.generate_batch(
        n_merchants=5,
        n_payments=200,
        n_anomalies=15,
    )

    # Persist to database
    try:
        gen.persist_to_db(dataset, repo.session)

        # Re-initialize tools with the new data
        if tools:
            pass  # Tools already have repo reference

        return {
            "status": "success",
            "message": f"Dataset loaded: {len(dataset.get('settlements', []))} settlements, "
                       f"{len(dataset.get('payments', []))} payments, "
                       f"{len(dataset.get('refunds', []))} refunds",
            "ground_truth_anomalies": len(dataset.get('ground_truth_anomalies', [])),
        }
    except Exception as e:
        logger.error(f"Error ingesting dataset: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to ingest dataset: {str(e)}")


@app.post("/api/reconcile/batch")
async def reconcile_batch(
    request: ReconcileBatchRequest,
    api_key: str = Depends(get_api_key),
):
    """Run deterministic reconciliation on all settlements in the loaded dataset.

    Returns reconciliation results for each settlement with variance classification.
    This is the foundation — always deterministic, never LLM.
    """
    global repo, tools

    if not repo or not tools:
        raise HTTPException(status_code=500, detail="API not initialized")

    # Get all settlements
    from ...db.models import Settlement
    settlements = repo.session.query(Settlement).all()

    if not settlements:
        raise HTTPException(status_code=404, detail="No settlements found. Ingest a dataset first.")

    from ...domain.reconciliation import ReconciliationEngine
    engine = ReconciliationEngine()

    results = []
    for settlement in settlements:
        try:
            recon_result = engine.reconcile_settlement(repo, settlement.settlement_id)
            results.append({
                "settlement_id": settlement.settlement_id,
                "merchant_id": settlement.merchant_id,
                "actual_amount": float(settlement.amount),
                "expected_amount": float(recon_result.expected),
                "variance": float(recon_result.variance),
                "status": recon_result.status,
                "confidence": recon_result.confidence,
            })
        except Exception as e:
            logger.error(f"Error reconciling {settlement.settlement_id}: {e}")
            results.append({
                "settlement_id": settlement.settlement_id,
                "error": str(e),
            })

    # Calculate match rate
    matched = sum(1 for r in results if r.get("status") == "MATCHED")
    match_rate = round(matched / len(results) * 100, 2) if results else 0

    return {
        "total_settlements": len(results),
        "matched": matched,
        "match_rate_percent": match_rate,
        "results": results,
    }


@app.post("/api/investigate/{case_id}")
async def investigate_case(
    case_id: str,
    api_key: str = Depends(get_api_key),
):
    """Trigger the investigation agent on a case.

    The agent runs through its ReAct loop: observe → hypothesize → gather evidence → test → conclude.
    Returns the investigation result with root cause, evidence, trace, and policy decision.
    """
    global agent

    if not agent:
        raise HTTPException(status_code=500, detail="API not initialized")

    try:
        result = agent.investigate(case_id)

        # Get case detail with evidence
        case = repo.get_case(case_id)
        evidence = repo.get_evidence_by_case(case_id)

        return {
            "case_id": case.case_id,
            "object_type": case.object_type,
            "object_id": case.object_id,
            "status": case.status,
            "root_cause": result.root_cause,
            "root_cause_type": result.root_cause_type,
            "confidence": result.confidence,
            "evidence": evidence,
            "recommended_action": result.recommended_action,
            "policy_reason": "Policy evaluated",
            "agent_trace": [
                {
                    "step_id": step.step_id,
                    "step_type": step.step_type,
                    "timestamp": step.timestamp,
                    "tool_name": step.tool_name,
                    "reasoning": step.reasoning,
                    "conclusion": step.conclusion,
                    "confidence": step.confidence,
                }
                for step in result.steps
            ],
            "hypothesis": result.hypothesis.hypothesis_type if result.hypothesis else None,
            "auto_resolved": result.auto_resolved,
        }
    except Exception as e:
        logger.error(f"Error investigating case {case_id}: {e}")
        # Return error result - case escalates
        return {
            "case_id": case_id,
            "status": "ESCALATED",
            "root_cause": "Investigation agent error",
            "root_cause_type": "SYSTEM_ERROR",
            "confidence": 0.0,
            "evidence": [],
            "recommended_action": "ESCALATED",
            "agent_trace": [],
            "hypothesis": None,
            "auto_resolved": False,
        }


@app.get("/api/cases/{case_id}")
async def get_case_detail(
    case_id: str,
    api_key: str = Depends(get_api_key),
):
    """Get full case detail including evidence and agent trace."""
    global repo

    if not repo:
        raise HTTPException(status_code=500, detail="API not initialized")

    case = repo.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    evidence = repo.get_evidence_by_case(case_id)

    return {
        "case_id": case.case_id,
        "object_type": case.object_type,
        "object_id": case.object_id,
        "status": case.status,
        "anomaly_type": case.anomaly_type,
        "root_cause": case.root_cause,
        "root_cause_type": case.root_cause_type,
        "confidence": float(case.confidence) if case.confidence else None,
        "variance": float(case.variance) if case.variance else None,
        "evidence": evidence,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
    }


@app.get("/api/cases", response_model=list[CaseResponse])
async def list_cases(
    status: Optional[str] = None,
    api_key: str = Depends(get_api_key),
):
    """List all cases, optionally filtered by status."""
    global repo

    if not repo:
        raise HTTPException(status_code=500, detail="API not initialized")

    cases = repo.list_cases_by_status(status) if status else repo.session.query(FinancialCase).all()

    results = []
    for c in cases:
        evidence = repo.get_evidence_by_case(c.case_id)
        results.append({
            "case_id": c.case_id,
            "object_type": c.object_type,
            "object_id": c.object_id,
            "status": c.status,
            "anomaly_type": c.anomaly_type,
            "root_cause": c.root_cause,
            "root_cause_type": c.root_cause_type,
            "confidence": float(c.confidence) if c.confidence else None,
            "variance": float(c.variance) if c.variance else None,
            "evidence": evidence,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        })

    return results


@app.post("/api/benchmark/run")
async def run_benchmark(
    api_key: str = Depends(get_api_key),
):
    """Run the full benchmark suite on loaded dataset.

    Reports match rate, root-cause accuracy, escalation recall,
    and the honest exception list of cases the agent couldn't resolve.
    """
    global repo, agent, policy_engine

    if not repo or not agent:
        raise HTTPException(status_code=500, detail="API not initialized")

    from ...eval.benchmark import Benchmark
    from ...eval.metrics import BenchmarkMetrics

    benchmark = Benchmark(repo, agent, FinancialCalculator())

    try:
        results = benchmark.run_benchmark(n_cases=50)

        return {
            "per_case_results": results["per_case_results"],
            "aggregate_metrics": results["aggregate_metrics"],
            "exception_list": results["aggregate_metrics"].get("exception_list", []),
            "total_cases_evaluated": len(results["per_case_results"]),
        }
    except Exception as e:
        logger.error(f"Benchmark error: {e}")
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {str(e)}")


@app.post("/api/benchmark/results")
async def get_benchmark_results(
    api_key: str = Depends(get_api_key),
):
    """Get the latest benchmark results.

    Returns the most recent benchmark output including match rate,
    root-cause accuracy, and exception list.
    """
    # In a production system, we'd cache the last benchmark results
    # For now, return a structure that the frontend can display
    return {
        "latest_run": None,
        "message": "Run /api/benchmark/run to execute the benchmark suite",
    }


# --- Startup ---


@app.on_event("startup")
async def startup_event():
    """Initialize database and default components on startup."""
    global repo, tools, agent

    db_path = os.environ.get("DB_PATH", "./ledgerlens.db")

    # Initialize database
    engine = init_db(db_path)

    # Create repository
    session = get_session(db_path)
    repo = Repository(session)

    # Initialize domain components
    calculator = FinancialCalculator()
    policy_engine = PolicyEngine()

    # Initialize hypothesis engine
    hypothesis_engine = HypothesisEngine()

    # Initialize tools
    tools = InvestigationTools(repo, calculator, policy_engine)

    # Initialize investigation agent
    agent = InvestigationAgent(repo, tools, hypothesis_engine, policy_engine)

    logger.info("LedgerLens API initialized successfully")