"""serendipity/metrics.py — the measurement the research text forgot.

You cannot claim "the echo chamber is broken" without numbers. These are
computed from the store's event log:

  coverage      — fraction of the catalog shown at least once (supply side)
  gini          — inequality of exposure across items (0 = perfectly even;
                  the lower, the less popularity-biased the system)
  entropy       — Shannon entropy of the exposure distribution, normalized
  slot stats    — shown/engaged per strategy (the router's learning signal)
  serendipity   — engagement rate on NON-exploit slots (did the weird
                  stuff actually land, or did we just spam the user?)
  intralist     — 1 − mean pairwise tag-Jaccard over the last lists
"""
import math
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple


def gini(counts: List[int]) -> float:
    """Gini coefficient of exposure counts. 0 = perfectly even."""
    if not counts:
        return 0.0
    ordered = sorted(counts)
    n = len(ordered)
    if n <= 1 or sum(ordered) == 0:
        return 0.0
    cum = 0
    for i, v in enumerate(ordered, start=1):
        cum += i * v
    return (2 * cum) / (n * sum(ordered)) - (n + 1) / n


def normalized_entropy(counts: List[int]) -> float:
    if not counts:
        return 0.0
    total = sum(counts)
    if total <= 0 or len(counts) <= 1:
        return 0.0
    ent = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * math.log(p)
    return ent / math.log(len(counts))


def exposure_report(catalog: List[str], shown_counts: Dict[str, int]) -> dict:
    counts = [shown_counts.get(item, 0) for item in catalog]
    shown = sum(1 for c in counts if c > 0)
    return {
        "catalog_size": len(catalog),
        "coverage": round(shown / len(catalog), 4) if catalog else 0.0,
        "gini_exposure": round(gini(counts), 4),
        "entropy_exposure": round(normalized_entropy(counts), 4),
    }


def slot_report(events: List[dict]) -> Dict[str, dict]:
    """Events carry signal ∈ {shown, engaged, ignored, negative}.
    "shown" events count exposure; reaction events count outcomes."""
    stats: Dict[str, dict] = {}
    for e in events:
        slot = e.get("slot", "?")
        s = stats.setdefault(slot, {"shown": 0, "engaged": 0, "ignored": 0, "negative": 0})
        sig = e.get("signal")
        if sig == "shown":
            s["shown"] += 1
        elif sig in ("engaged", "ignored", "negative"):
            s[sig] += 1
    for slot, s in stats.items():
        s["engagement_rate"] = round(s["engaged"] / s["shown"], 4) if s["shown"] else 0.0
    return stats


def serendipity_rate(events: List[dict]) -> Optional[float]:
    """Engagement rate on non-exploit slots (shown events = denominator,
    engaged reactions = numerator). None if nothing explored yet."""
    shown = engaged = 0
    for e in events:
        if e.get("slot") != "exploit" and e.get("signal") == "shown":
            shown += 1
        elif e.get("slot") != "exploit" and e.get("signal") == "engaged":
            engaged += 1
    return round(engaged / shown, 4) if shown else None


def interest_tags(events: List[dict], tags_of: Callable, top: int = 3) -> List[str]:
    """User's top tags among ENGAGED items."""
    counts: Counter = Counter()
    for e in events:
        if e.get("signal") == "engaged":
            for t in tags_of(e.get("item", "")):
                counts[t] += 1
    return [t for t, _ in counts.most_common(top)]
