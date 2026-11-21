# serendipity-engine

A plug-and-play exploration/serendipity engine for a chatbot that
"revolves solely around the user".

It implements, in-process and dependency-free, the mechanisms that
YouTube / Instagram / Pinterest use to break their own echo chambers:

| Mechanism (from the research) | Where |
|---|---|
| UCB "100-view engine" + Thompson Sampling (Beta draws) | `serendipity/bandit.py` |
| Pixie-style biased random walks, walk length L as the serendipity dial | `serendipity/walk.py` |
| Instagram concept taxonomy: parent / sibling / cousin stepping + multimodal bridges | `serendipity/taxonomy.py` |
| MMR + 0.5^k category decay + the 0.3x same-category re-roll rule | `serendipity/diversity.py` |
| 4-slot weighted router (60/20/15/5) that adapts its mixture to engagement | `serendipity/router.py` |
| α/β feedback loop with attribution tokens (one reaction per suggestion) | `serendipity/engine.py` |
| Safety filters, quality bar for wildcards, blocked tags/categories | `serendipity/engine.py` |
| Coverage / Gini / entropy / serendipity-rate measurement | `serendipity/metrics.py` |
| Binding to the existing ICE/Hari `CuriosityGraph` (no ice/ edits) | `ice_adapter/adapter.py` |

## 30-second start

```python
from serendipity import SerendipityConfig, SerendipityEngine, Item

engine = SerendipityEngine(
    config=SerendipityConfig(seed=42, store_path="serendipity.sqlite"),
    items=[Item("t1", "Stone sculpture basics", ("sculpture", "art"), "sculpture", 0.8)],
    taxonomy={"sculpture": "fine_arts", "fine_arts": "culture", "culture": ""},
)

picks = engine.suggest("user_1", n=2, context_tags=("painting",))
for p in picks:
    print(p.slot, "-", p.item.title, "|", p.reason)   # show it, or just log it

# ... the user reacts (your code decides which signal) ...
engine.record(picks[0].token, "engaged")   # or "ignored" / "negative"
```

## Run the proof

```bash
python3 -m unittest discover -s tests      # 36 tests
python3 demo/demo_chat.py                  # echo-chamber vs serendipity A/B
python3 demo/demo_chat.py --ice            # + live CuriosityGraph, dead dial revives
```

The demo prints a per-turn log (slot, reaction, reason), the
ECHO-vs-FULL comparison table, and — with `--ice` — the real
`engine/curiosity_graph.CuriosityGraph` running through the adapter,
including the previously-dead `exploration_progress` field moving for
the first time.

## Where things plug in

See [INTEGRATION.md](INTEGRATION.md) for the exact wiring into the
ICE/Hari stack (turn loop, feedback classification, graph sync), and
[docs/TOOL_CHOICES.md](docs/TOOL_CHOICES.md) for the honest
build-vs-adopt analysis (Mab2Rec, Vowpal Wabbit, Gorse, Qdrant, Neo4j,
LangChain) — including when this in-process engine is the right-sized
wheel and when you should graduate to a bigger one.

## Honest notes

- Reason strings are honest but simple; on a cold start with no
  context, several zero-information items tie and the tie-break is
  deterministic-by-id. The MECHANISM (scores, slots, feedback) is exact;
  the prose is cosmetic.
- `intralist_diversity` reads ~1.0 in the demo because the demo's tag
  sets barely overlap; with real embeddings the metric discriminates
  much better.
- No dependencies. Python stdlib only. The Beta sampler is
  `random.betavariate` — an existing, maintained implementation; we
  orchestrate it rather than reinventing it.
