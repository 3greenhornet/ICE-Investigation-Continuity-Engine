# TOOL_CHOICES.md — build vs adopt: the honest analysis

Your instinct ("don't reinvent the wheel, the goal is the mechanism
working") is right — applied precisely, it says: use existing engines
where the wheel is actually big, and stop short where it isn't. Here is
the line, tool by tool.

## What this package deliberately reuses (not reinvented)

- **Beta sampling**: `random.betavariate` (Python stdlib) and
  `numpy.random.beta` both exist, are maintained and tested. We call
  them; we never hand-roll a distribution.
- **The interest graph**: your repo's `CuriosityGraph` — Postgres-backed,
  Hebbian edge learning, session isolation. The adapter READS it; we do
  not build a second graph.
- **Vector math**: cosine/Jaccard in `diversity.py` are 10-line
  primitives; calling a vector DB for a 200-node session graph would be
  the opposite of plug-and-play.

## When to graduate to the bigger wheels

| Tool | Adopt when | What you gain | What you pay | Migration path from this package |
|---|---|---|---|---|
| **Qdrant / OpenSearch / Weaviate MMR** | catalog > ~50k items with real embeddings; you already run a vector DB | native MMR at `diversity=0.6` parameter, ANN speed | a service to operate; embeddings for everything | move `engine._similarity_fn` to a DB call; keep slots/bandits/feedback here |
| **LangChain / LlamaIndex MMR** | your retrieval already lives in one of these frameworks | `.max_marginal_relevance_search(fetch_k=50, lambda_mult=0.4)` for free | framework lock-in for a 10-line MMR | same as above — only the similarity function moves |
| **Vowpal Wabbit (`--cb_explore_adf`)** | you need CONTEXTUAL bandits at high throughput (features per decision, online learning at scale) | battle-tested exploration policies incl. contextual features, Azure Personalizer-grade | input-file format, C++ service or daemon, feature engineering | replace `bandit.score_all` in the uncertainty lane; keep the router |
| **Mab2Rec / Mabwiser** | you want feature-rich bandit policies from a library (Mab2Rec is built on Mabwiser) | LinUCB/TS with arm features, offline evaluation tools | pip dep + feature vectors for every item | swap `bandit.py`; the store already keeps (α, β) per arm |
| **Gorse** | you want a standalone recommender microservice with REST API and click-stream collaborative filtering; multi-app, multi-user at scale | Docker one-liner, YAML explore ratios, popular/latest/random mixing out of the box | a Go service + Postgres + feeding it your events; it wants ITEM IDs and click logs, not chat turns | feed it `store.events()` as read/like feedback; keep this engine as the chat-side router |
| **Neo4j GDS (`gds.randomWalk`)** | your graph is already in Neo4j, or multi-hop queries need index-backed traversal at >100k nodes | `gds.randomWalk.stream` with `walkLength`, Node2Vec | a graph DB server | replace `walk.biased_random_walk`; the interface (adjacency in, visit scores out) stays |
| **Recombee / Amazon Personalize** | managed SaaS preferred over hosting anything; budget exists | exploration/serendipity sliders in the API payload | per-event pricing, data leaves your stack | same as Gorse — event feed out, suggestions in |

## The decision rule

At the scale of a personal chatbot (tens to hundreds of live topics per
session), the in-process engine is the SMALLER wheel: zero services,
zero deps, full control of the dials, and the feedback loop lands in the
same process as your conversation state. Every "bigger" tool above adds
an operating cost that only pays for itself with scale, features, or
multi-system sharing you do not have yet.

When you DO graduate, only one function moves at a time (the table's
last column says which). That is the whole point of the seam layout:
`bandit.py`, `walk.py`, and `diversity.py` are replaceable leaves; the
router, the store, the attribution tokens and the metrics are yours to
keep.
