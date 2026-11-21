"""Optional FastAPI service over the chatbot (ICE-style thin routes).

Run: ``uvicorn recommender.service:app --port 8090``
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .chatbot import Chatbot
from .config import RecommenderConfig

app = FastAPI(
    title="Diversity-first chatbot recommender",
    version="0.1.0",
    description=(
        "similar-but-different + wildcards + anti-repetition, each backed "
        "by a real engine (Qdrant / networkx / Mab2Rec or Vowpal Wabbit)."
    ),
)

_bot: Chatbot | None = None


def get_bot() -> Chatbot:
    global _bot
    if _bot is None:
        _bot = Chatbot(RecommenderConfig())
    return _bot


class ChatRequest(BaseModel):
    message: str = Field(..., examples=["I've been watching oil painting videos"])
    focus_category: Optional[str] = None
    k: Optional[int] = None


class FeedbackRequest(BaseModel):
    item_id: str
    verdict: Literal["liked", "ignored", "disliked"]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/engines")
def engines() -> dict:
    return get_bot().engine_report()


@app.post("/chat")
def chat(request: ChatRequest) -> dict:
    bot = get_bot()
    reply = bot.chat(request.message, focus_category=request.focus_category,
                     k=request.k)
    payload = reply.to_dict()
    payload["engines"] = bot.engine_report()
    return payload


@app.post("/feedback")
def feedback(request: FeedbackRequest) -> dict:
    bot = get_bot()
    accepted = bot.feedback(request.item_id, request.verdict)
    if not accepted:
        raise HTTPException(
            status_code=404,
            detail=f"item {request.item_id} was not in the last reply",
        )
    return {"accepted": True, "stats": bot.stats()}


@app.get("/stats")
def stats() -> dict:
    return get_bot().stats()
