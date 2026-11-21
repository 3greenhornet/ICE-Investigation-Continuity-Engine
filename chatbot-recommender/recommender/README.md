# Diversity-first chatbot recommender

The answer to "the bot only revolves around the user, it's boring".

Three mechanisms, each backed by a real open-source engine (install and
customize -- never reinvent):

| # | Mechanism | What it does | Engine |
|---|-----------|--------------|--------|
| 1 | **similar-but-different** | user watches paintings -> bot recommends sculpture, photography, architecture -- the *neighbour* categories (graph hop), never more of the same; the result set is internally diversified by MMR | **Qdrant** (embedded vector search) + **networkx** (category graph) + MMR (the algorithm LangChain exposes, implemented directly) |
| 2 | **wildcards** | occasional slots from four pools: long-tail old items ("old video with 100 plays"), unknown authors (<150 followers), orthogonal topics (graph-far categories), pure random. Which pool -- and whether a wildcard fires at all -- is decided per turn by a contextual bandit that learns from feedback | **Mab2Rec** (MABWiser LinTS) by default; **Vowpal Wabbit** (`--cb_explore`) as an alternative; **Gorse** adapter env-gated for deployment |
| 3 | **anti-repetition** | nothing shown ever returns (hard seen-set), and items *semantically similar* to recently-shown ones are suppressed with a decaying penalty | port of the Hari engine's `semantic_refractory` idea (cosine suppression, refractory window) |

## Quick start

```bash
pip install -r requirements.txt
python demo.py            # scripted session showing all three mechanisms
python -m pytest          # 45 tests
uvicorn recommender.service:app --port 8090   # HTTP service
```

```python
from recommender import Chatbot
bot = Chatbot()
reply = bot.chat("I've been watching oil painting videos")
print(reply.text)                          # recommendations + honest reasons
bot.feedback(reply.recommendations[0].item.id, "liked")   # trains the bandit
```

## The wildcard arms (mechanism 2)

```
none | long_tail_old | unknown_author | orthogonal_topic | pure_random
```

Every turn the bandit picks one arm given the user-state context
(4 dims: wildcard success rate, turns since last wildcard, history
diversity, exploit engagement rate). Rewards: liked=+1, ignored=0,
disliked=-0.5. The `none` arm competes with the exploit slots'
engagement rate, so if wildcards consistently flop the bot *learns* to
stop forcing them. Cold start is uniform (honest exploration) until
every arm has one observation; after that Mab2Rec fits and updates
online via `partial_fit`.

## Engine swap

```python
from recommender import Chatbot, RecommenderConfig
cfg = RecommenderConfig(bandit_engine="vowpal_wabbit")   # or "epsilon", "gorse"
cfg = RecommenderConfig(vector_engine="numpy")           # fallback, no qdrant
cfg = RecommenderConfig(graph_engine="neo4j")            # needs NEO4J_URI
bot = Chatbot(cfg)
```

Env-gated remote engines (fail loudly, never silently):
* `GORSE_ENDPOINT` -- delegate exploration to a live Gorse server
* `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` -- Neo4j GDS category graph

## Layout

```
recommender/
  config.py                    every knob (ratios, thresholds, engine names)
  catalog.py                   items, authors, long-tail pools
  demo_data.py                 seeded 101-item catalog incl. the user's examples
  embeddings.py                TF-IDF embedder (swap seam for neural encoders)
  engines/
    vector_store.py            Qdrant (real) / numpy (fallback)
    bandit.py                  Mab2Rec / Vowpal Wabbit / epsilon / Gorse
    category_graph.py          networkx / Neo4j
  mechanisms/
    similar_but_different.py   neighbour-category retrieval + MMR
    wildcards.py               four pools + the bandit decision
    anti_repetition.py         hard seen-set + semantic refractory (Hari port)
  mixer.py                     feed composition + feedback loop
  chatbot.py                   conversational facade
  service.py                   FastAPI: /chat /feedback /engines /stats /health
tests/                         45 tests: every mechanism contract pinned
demo.py                        the runnable story
```

## Honest limitations

* **TF-IDF, not deep embeddings.** The swap seam exists (`Embedder`
  protocol); a sentence-transformers encoder drops in where disk/model
  download is available. The category graph carries the
  painting->sculpture semantics structurally, so the demo behaviour does
  not depend on embedding quality.
* **MMR is implemented directly** (~30 lines) rather than importing
  LangChain for one function -- same algorithm, no heavyweight dep.
* **Gorse / Neo4j adapters are env-gated**, not runnable in this sandbox
  (no Docker / no server). They exist so deployment switches to them
  without touching mechanism code.
* **The demo catalog is synthetic** (101 items, 13 categories) but
  deliberately contains the user's exact examples: ~100-play old
  videos, unknown authors, unrelated topics.
* Focus inference is TF-IDF nearest-neighbour voting -- no LLM in the
  loop yet. An LLM interest-parser can slot in front of
  `SimilarButDifferent.infer_focus` later.
