"""serendipity — a plug-and-play exploration/serendipity engine.

Drop-in layers for a chatbot that "revolves solely around the user":
Thompson Sampling / UCB (the 100-view engine), Pixie-style random walks
with a walk-length dial (painting -> sculpture), Instagram-style taxonomy
stepping + multimodal bridges, MMR + category decay (stop the echo), a
4-slot weighted router that adapts its mixture to engagement, and the
measurement layer (coverage / Gini / serendipity rate) the research
forgot.

Zero required dependencies: pure stdlib. See README.md for the
30-second start and INTEGRATION.md for wiring into the ICE/Hari stack.
"""
from .config import SerendipityConfig
from .engine import SerendipityEngine
from .models import FeedbackSignal, Item, Suggestion

__all__ = [
    "SerendipityConfig", "SerendipityEngine",
    "FeedbackSignal", "Item", "Suggestion",
]
