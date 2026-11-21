# INTEGRATION.md — wiring this into your chatbot (Antigravity workflow)

You asked: "that needs integration into the existing file yes??"

**Yes.** The engine is standalone (it never imports your repo), but it
does nothing until three things happen in YOUR code. That's by design —
it's the same as installing someone's calendar app and then adding your
own events inside it. The three touchpoints:

1. **Feed it your topics** (once + whenever the graph evolves)
2. **Ask it for suggestions** (once per conversational beat)
3. **Tell it how the user reacted** (once per suggestion)

Everything else — slot drawing, UCB math, walks, decay, safety, metrics,
persistence — is the engine's job.

---

## Path A (recommended): outside the turn loop — ZERO ice/ edits

Your repo already runs `TurnPipeline` from `engine/generate.py` as a
plain object (see `scripts/diversity_check.py` in your repo for the
pattern). Keep that boundary and drive the adapter from your own loop:

```python
# your_chat_loop.py  (new file — nothing in ice/ changes)
import asyncio
from engine.curiosity_graph import get_graph_manager, close_session_graph
from ice_adapter.adapter import IceSerendipityAdapter

async def chat_session(session_id: str, user_text: str, turn: int):
    graph = await get_graph_manager(session_id)      # the REAL graph
    adapter = IceSerendipityAdapter(session_id, graph)
    engine = _ENGINES.get(session_id)
    if engine is None:
        engine = adapter.build_engine(store_path=f"state/serendipity_{session_id}.sqlite")
        _ENGINES[session_id] = engine

    # -- 1. let the cognitive engine do its normal turn ----------------
    # (your existing TurnPipeline call stays exactly as it is)

    # -- 2. ask the serendipity engine for 1-2 proactive topics --------
    picks = engine.suggest(
        session_id, n=1,
        context_tags=tuple(w for w in user_text.lower().split() if len(w) > 3),
    )

    # -- 3. when the user reacts, classify and report ------------------
    # simple classifier; upgrade with your sentiment/intent code later
    def classify(next_user_text: str, was_topic_taken_up: bool) -> str:
        if was_topic_taken_up:
            return "engaged"
        if next_user_text and next_user_text.strip():
            return "ignored"        # they changed the subject
        return "ignored"

    return picks   # surface the topic in YOUR response layer
```

Reaction timing: record the feedback when the NEXT user message arrives
(or immediately for explicit thumbs-down). One reaction per token —
`engine.record(token, signal)` raises on double-record by design.

Graph freshness: the curiosity graph learns while you chat. Refresh the
engine's view every few turns:

```python
if turn % 5 == 0:
    adapter.sync()     # re-reads nodes + edge weights, registers new nodes
```

## Path B (optional): in-pipeline proactive injection — 2 small pastes

If you want Hari's own monologue/volition channels to carry the
suggestion instead of your response layer, paste into
`engine/generate.py::TurnPipeline.execute`, right after the workspace
allocation step (~"12. Workspace allocation" in your copy):

```python
# --- serendipity hook (optional) -----------------------------------
try:
    if self._serendipity is not None and not is_tick:
        picks = self._serendipity.suggest(
            self.session_id, n=1,
            context_tags=tuple(w for w in (user_input or "").lower().split() if len(w) > 3),
        )
        if picks:
            self._pending_suggestion = picks[0]   # expression layer can voice it
except Exception:
    pass
```

and initialize `self._serendipity = None` in `__init__` (set it from your
session bootstrap with the adapter). If you skip Path B entirely the
engine still works via Path A.

## What the adapter touches (and never touches)

Reads: `graph._graph` nodes (`core_question`, `importance`,
`exploration_progress`) and edge weights.
Writes (through the graph's own designed fields — all persisted by
CuriosityGraph's existing sync worker):
- `exploration_progress` +0.25 on engagement, +0.10 on ignore — the
  field ICE's attention code already multiplies by `(1 - progress)` but
  which NOTHING in the repo ever updated. Your attention math starts
  differentiating "enough about this" for the first time.
- edge weights +0.05 between co-engaged nodes (Hebbian; the update path
  existed but was never driven by user reactions).
- `importance` x0.7 on explicit negative.

Never touches: db schema, migrations, case_engine, psyche state, your
prompts, your LLM calls.

## Tuning quick-reference

| Dial | Config field | Effect of raising it |
|---|---|---|
| Exploration constant c | `ucb_c` | colder items get bigger spikes (more 100-view surfacing) |
| Walk length L | `walk_length` | 2 = same cluster, 5-6 = cross-domain drift |
| MMR λ | `mmr_lambda` | 1.0 = pure relevance, 0.4 = force variety |
| Wildcard share | `weights["entropy"]` | more pure-random (capped by `entropy_ceiling`) |
| Category decay base | `decay_base` | lower = categories burn out faster |
| Quality bar for wildcards | `entropy_min_quality` | higher = safer random lane |

Full formulas and the research-text traceability matrix:
[docs/ALGORITHMS.md](docs/ALGORITHMS.md).

## If your stack is NOT the Hari engine

The core is stack-agnostic. Anything that can call three functions can
host it: OpenAI-style tool calling (wrap `suggest` + `record` as two
tools, pass the token through), LangChain (a custom Tool), FastAPI (a
2-endpoint microservice). The `Item` model maps to any topic inventory.
The only ICE-specific file is `ice_adapter/adapter.py` — swap or ignore
it.
