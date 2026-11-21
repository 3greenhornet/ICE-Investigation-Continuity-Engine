# ICE - Investigation Continuity Engine

ICE is a persistent investigation workspace for preserving case state, evidence,
claims, hypotheses, contradictions, decisions, and next-best actions across
sessions. It combines a PostgreSQL-backed case engine with a FastAPI projection
plane, a Streamlit interface, an experimental serendipity engine, and a
diversity-first chatbot recommender.

## Repository layout

- `ice/` - main Investigation Continuity Engine package and application
- `chatbot-recommender/` - standalone diversity-first chatbot recommender
- `scripts/` - workspace-level smoke-test and verification helpers
- `GAP-ACCOUNTING.md` - gap and requirement accounting
- `SELF-EVALUATION.md` - implementation self-assessment
- `worklog.md`, `worklog_nov21.md` - development history

## Main application

The primary application lives in [`ice/`](ice/). Start with its
[README](ice/README.md) and [AGENTS.md](ice/AGENTS.md) for setup, architecture,
and implementation guardrails.

Typical setup:

```powershell
cd ice
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cp .env.example .env
```

The ICE service requires PostgreSQL and pgvector. Docker-based setup and migration
instructions are documented in the [ICE README](ice/README.md).

## Chatbot recommender

The standalone recommender has its own quick start and test suite:

```powershell
cd chatbot-recommender/recommender
python -m pip install -r requirements.txt
python demo.py
python -m pytest
```

See [chatbot-recommender-README.md](chatbot-recommender-README.md) for the
architecture and mechanism details.

## Validation

Run the narrowest relevant test suite for a change. For the main application:

```powershell
cd ice
python -m pytest tests/unit
```

Integration verification additionally requires a running PostgreSQL instance.
