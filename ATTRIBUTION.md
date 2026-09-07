# ATTRIBUTION

LedgerLens is built on LoanLens AI (https://github.com/rrahul1011/loanlens_ai)

LedgerLens AI is MIT licensed. Original copyright © 2025 LoanLens.

We reuse: FastAPI structure, FAISS/LangChain RAG infrastructure, Logger utilities.

All financial domain logic, agents, and product functionality are original.

## License

MIT License — see LICENSE file for details.

## Dependencies and Licenses

| Library | License | Status |
|---|---|---|
| FastAPI | MIT | Safe |
| LangChain / langchain-community | MIT | Safe |
| FAISS (langchain-community) | MIT | Safe |
| sentence-transformers | Apache 2.0 | Safe |
| SQLAlchemy | MIT | Safe |
| Pydantic | MIT | Safe |
| Streamlit | Apache 2.0 | Safe |
| Faker | MIT | Safe |
| Google Generative AI SDK | Apache 2.0 | Safe |
| scikit-learn | BSD | Safe |

## Original Work

This project was built as the LedgerLens entry for the Razorpay AI Buildathon 2026, Track 04: AI Finance Controller.

Core original components:
- SyntheticFinancialDataGenerator
- ReconciliationEngine (deterministic Decimal arithmetic)
- AnomalyDetector (rule-based)
- FinancialCalculator (Python Decimal)
- InvestigationAgent (ReAct loop with 6 tools)
- HypothesisEngine (competing hypotheses + evidence scoring)
- EvidenceSystem (source-linked provenance)
- PolicyEngine (automation boundaries)
- CaseManager (state machine)
- Benchmark suite (match rate, root-cause accuracy, exception list)
- FastAPI backend + Streamlit frontend