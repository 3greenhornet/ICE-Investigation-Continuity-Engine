"""app/main.py — FastAPI application entrypoint (spec Part 4 §13.2).

Owner: ICE revamp, Part 4 §13.2.
Purpose: build the FastAPI app: lifespan-managed DB pool, CORS, the single
    catch-all exception handler, and mounting of all 11 routers under
    /api/v1. Route logic lives in app/routes/, services in app/deps.py.
Run:    uvicorn app.main:app --reload
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.errors import register_exception_handlers
from app.routes import (
    actions, cases, claims, contradictions, decisions, evidence, gaps,
    goals, health, hypotheses, investigation, reasoning, replay,
)
from db.connection import close_db, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("ICE database pool initialized")
    yield
    await close_db()
    logger.info("ICE database pool closed")


app = FastAPI(
    title="Investigation Continuity Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8501").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

PREFIX = "/api/v1"
app.include_router(cases.router, prefix=PREFIX, tags=["cases"])
app.include_router(evidence.router, prefix=PREFIX, tags=["evidence"])
app.include_router(claims.router, prefix=PREFIX, tags=["claims"])
app.include_router(hypotheses.router, prefix=PREFIX, tags=["hypotheses"])
app.include_router(contradictions.router, prefix=PREFIX, tags=["contradictions"])
app.include_router(gaps.router, prefix=PREFIX, tags=["gaps"])
app.include_router(investigation.router, prefix=PREFIX, tags=["investigation"])
app.include_router(actions.router, prefix=PREFIX, tags=["actions"])
app.include_router(decisions.router, prefix=PREFIX, tags=["decisions"])
app.include_router(replay.router, prefix=PREFIX, tags=["replay"])
# Hari ICE improvements pass: reasoning-loop surface (induction,
# predictions, timeline, ACH/causal, workspace, search, plan,
# introspection/trajectory/diagnostics, mechanism lab).
app.include_router(reasoning.router, prefix=PREFIX, tags=["reasoning"])
# Hari ICE improvements pass, goal-system phase: the Evolving Project
# Model (goals, clarity, provenance, maturity, reconciliation, intent
# profile) + STORM-lite research mode.
app.include_router(goals.router, prefix=PREFIX, tags=["goals"])
app.include_router(health.router, prefix=PREFIX, tags=["health"])