# ALGORITHMS.md — formulas, dials, and traceability to the research

## 1. Traceability matrix (research text → module → test)

| Research mechanism | Implementation | Proven by |
|---|---|---|
| UCB: Score(i) = μᵢ + c·√(ln N / nᵢ); nᵢ small ⇒ massive score; surfaces 100-view items | `bandit.ucb_score` (+inf at n=0, ln N ≥ 0 guard at N=0) | `test_ucb_cold_item_outranks_everything`, `test_ucb_bonus_shrinks_with_exposure`, engine test `test_uncertainty_slot_surfaces_cold_items` |
| Thompson Sampling: Beta(α,β) draws; flat curve (α=1,β=1) occasionally beats a 0.95 favorite | `bandit.thompson_draw` via stdlib `random.betavariate`, priors 1/1 | `test_thompson_flat_beta_sometimes_wins` (4000 rounds, 1–15% win band) |
| α ← α+1 on reply/click; β ← β+1 on ignore; dislike hurts more | `engine.record` (per-user arms; `negative_beta_weight`=2) | `test_feedback_loop_updates_arms_and_slots`, `test_negative_weighs_double` |
| Global vs per-user split (our addition, fixing the research's conflation) | two tables in `store`; global feeds the uncertainty lane, per-user feeds the exploit lane | engine e2e tests |
| Pixie: biased random walk; walk length L = serendipity dial (L=2 hyper-relevant, L=5-6 drift) | `walk.biased_random_walk(walk_length, restart_prob)` over the host graph | `test_walk_length_controls_reach`, `test_restart_keeps_walk_anchored` |
| Instagram concept hierarchy: Child → Parent → Siblings (painting → sculpture) | `taxonomy.Taxonomy.siblings/cousins/adjacent_categories` | `test_siblings_painting_vs_sculpture`, `test_cousins_cross_family` |
| Instagram multimodal intersection (bridging two interests) | `taxonomy.multimodal_bridges` (≥2 interest-tag hits) | `test_bridges_need_two_hits`; demo shows "Bridges your art + painting" |
| MMR: argmax[λ·Sim₁(Dᵢ,User) − (1−λ)·max Sim₂(Dᵢ,Dⱼ)] | `diversity.mmr_select` (λ dial), applied to every slot pool + within-batch >0.75 similarity skip | `test_mmr_favors_variety_over_greedy` |
| Cluster downranking: score × 0.5^k (k = recent appearances) | `diversity.decay_multiplier(base=0.5)` | `test_decay_multiplier` |
| Same category in last 2 turns ⇒ 0.3x and re-roll | `diversity.reroll_needed` + engine `_pick` skip chain | `test_same_category_not_suggested_back_to_back` |
| 4-slot router: 60% exploit / 20% graph-sibling / 15% UCB-TS / 5% pure entropy | `router.draw_slots` over `config.weights` | `test_draw_distribution_matches_weights` (20k draws, ±2%) |
| (beyond spec) mixture should learn: adaptive router | `router.effective_weights` — Laplace rate × base, bounded projection with floors | `test_adaptive_shifts_toward_winning_slot`, `test_adaptive_ignores_thin_evidence` |
| 5% pure entropy from bottom 10% least-used, ignoring profile | engine entropy pool, `entropy_pool_fraction` | engine tests + demo (`DEMO VERDICT: PASS`) |
| (the gap the research forgot) safety on random picks | `entropy_min_quality`, blocked tags/categories | `test_safety_never_surfaces_junk`, `test_entropy_respects_quality_bar` |
| (the gap the research forgot) measurement | `metrics.py`: coverage, Gini, entropy, serendipity rate, slot stats | `test_metrics_report_shape`, demo table |

## 2. The full formulas as implemented

**UCB (uncertainty lane, global counters):**
```
Score(i) = μᵢ + c·√(ln N / nᵢ)
    nᵢ = global shown count (store + item's n_shown_global prior)
    N  = total shown across all items
    nᵢ = 0  →  +inf   (the coldest item outranks everything once)
    N  = 0  →  ln N treated as ln 1 = 0
```

**Thompson (per-item, when `policy="thompson"`):**
```
draw ~ Beta(α_global + 1, β_global + 1)      # flat at (1,1) for strangers
per-user arms:  α += 1 on engaged; β += 1 on ignored; β += 2 on negative
posterior mean (exploit backbone) = (α+1)/(α+β+2)
```

**Biased random walk (adjacent lane):**
```
per step: with prob restart_prob → jump to a random seed
          else → neighbor j with P(j) ∝ weight(current, j)
L = walk_length (the dial); seeds = the user's last engaged items;
visits are counted over walks_per_seed × |seeds| walks and normalized.
```

**Adjacent-lane relevance for item x:**
```
0.75                      if x bridges ≥2 of the user's top interest tags
0.55 + 0.45·walk_score    if x was reached by a walk
0.65                      if x's category is a sibling/cousin of an engaged category
0.55                      if x is near the live conversation (cold user, no history)
```

**Exploit-lane relevance:**
```
rel(x) = 0.6·posterior_mean(x) + 0.4·affinity(x, context)
affinity = tag-Jaccard(context, x) or embedding cosine when vectors exist
```

**Repetition suppression (before a candidate is accepted):**
```
score × 0.5^k                    k = appearances of its category in the recent window
category ∈ last 2 turns          → candidate skipped, next in pool tried (the 0.3x re-roll)
same item within dedupe window   → skipped
similarity to a batch-mate >0.75 → skipped (MMR hard form)
```

## 3. Known-unknowns (honesty section)

- Free-text curiosity questions have no categories until the adapter's
  keyword bootstrap guesses one; hosts with a real categorizer should
  call `set_taxonomy()` / tag items properly.
- `n_shown_global` on Items is a prior; the store's counters are the
  live truth. The adapter maps `exploration_progress × 10` as the prior
  — a bootstrap choice, documented in `ice_adapter/adapter.py`.
- The entropy lane samples uniformly from the bottom-10% pool; it does
  NOT model topical distance from the user (that is the adjacent lane's
  job). The research's "bottom 10% least-used, ignoring profile vectors"
  is implemented literally.
