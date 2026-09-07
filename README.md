# LedgerLens — AI Finance Controller

## Overview

LedgerLens is an AI financial investigator that reconstructs the causal chain behind payment anomalies, proves root cause with evidence, and closes the reconciliation loop with a measured match rate and an honest exception list.

## Problem

Finance teams stare at mismatched numbers every day. Current tools tell them *what* doesn't match. LedgerLens tells them *why*. It ingests a batch of synthetic Razorpay-style financial events, runs deterministic reconciliation, detects anomalies, then launches an AI investigation agent that reconstructs the causal chain, scores evidence for each hypothesis, calculates the exact financial impact, and closes or escalates the case.

## Track

**Track 04 — AI Finance Controller**
- Closes a finance-ops loop across 50+ synthetic records
- Reports match rate and an honest exception list
- Multi-source reconciliation as the primary direction

## Quick Start

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment variables
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# 4. Initialize the database
python -m ledgerlens.backend.src.db.init_db

# 5. Generate synthetic dataset
python -m ledgerlens.dataset.generator --seed 42 --payments 200 --anomalies 15

# 6. Start the backend
uvicorn ledgerlens.backend.src.api.main:app --reload --port 8000

# 7. Start the frontend (separate terminal)
streamlit run ledgerlens/frontend/app.py
```

## Demo

The demo flow:

1. **Batch Summary**: Load the sample dataset and view 50+ settlements with anomaly detection
2. **Investigation**: Select a settlement with a variance and run the AI investigator
3. **Agent Trace**: View the step-by-step ReAct loop: observe → hypothesize → gather evidence → test → conclude
4. **Policy Decision**: See the deterministic policy engine evaluate the case (auto-resolve < ₹500, escalate ≥ ₹500)
5. **Benchmark**: Run the full benchmark suite to see match rate, root-cause accuracy, and the exception list

## Architecture

- **Frontend**: Streamlit — 4 views (batch summary, investigation workspace, agent trace, human review queue)
- **Backend**: FastAPI + SQLite — deterministic reconciliation engine, investigation agent, policy engine
- **Data**: SQLite with SQLAlchemy ORM — 12 relational tables + financial_edges adjacency model
- **LLM**: Google Gemini Flash — for hypothesis generation and reasoning (never for arithmetic)
- **Financial Arithmetic**: Python `Decimal` — exact, deterministic, non-negotiable
- **Agent Pattern**: ReAct (Reason + Act) loop with 10-iteration cap and evidence provenance

## Key Features

- Deterministic reconciliation (LLM never touches arithmetic)
- Causal investigation with competing hypotheses and evidence provenance
- Policy-enforced automation boundaries (auto-resolve < ₹500, escalate ≥ ₹500)
- Honest exception list (cases the agent couldn't resolve)
- Agent trace log (fully visible step-by-step)
- Benchmarkable metrics (match rate, root-cause accuracy, escalation recall)
- One deliberate failure and recovery scenario

## Demo Credentials

- API Key: `dev_key_12345` (header: `X-API-Key`)
- Default dataset: seed 42, 5 merchants, 200 payments, 15 anomalies