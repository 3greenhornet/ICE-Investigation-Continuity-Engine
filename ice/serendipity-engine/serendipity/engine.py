"""serendipity/engine.py — the facade. One object, three calls:

    engine.suggest(user, n=3, context_tags=...)   -> [Suggestion, ...]
    engine.record(token, "engaged")               -> summary dict
    engine.metrics(user)                          -> measurement dict

Everything the host must NOT have to think about lives here: slot
drawing, candidate pools, safety, dedupe, decay, re-roll, MMR,
attribution tokens, the audit log, and the on_feedback hook the ICE
adapter uses to push learning back into the CuriosityGraph.
"""
import random
import threading
import uuid
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .bandit import posterior_mean, score_all
from .config import SerendipityConfig
from .diversity import (
    decay_multiplier,
    default_similarity,
    intralist_diversity,
    mmr_select,
    reroll_needed,
    tag_jaccard,
)
from .metrics import (
    exposure_report,
    interest_tags,
    serendipity_rate,
    slot_report,
)
from .models import DecisionRecord, FeedbackSignal, Item, Suggestion
from .router import SLOTS, draw_slots, effective_weights
from .store import make_store
from .taxonomy import Taxonomy, multimodal_bridges
from .walk import biased_random_walk

FALLBACK_ORDER = ("entropy", "adjacent", "uncertainty", "exploit")


class SerendipityEngine:
    def __init__(
        self,
        config: Optional[SerendipityConfig] = None,
        items: Optional[List[Item]] = None,
        adjacency: Optional[Dict[str, List[Tuple[str, float]]]] = None,
        taxonomy: Optional[Dict[str, str]] = None,
        on_feedback: Optional[Callable] = None,
    ) -> None:
        self.cfg = config or SerendipityConfig()
        self.cfg.validate()
        self._items: Dict[str, Item] = {}
        self._adjacency: Dict[str, List[Tuple[str, float]]] = dict(adjacency or {})
        self._taxonomy = Taxonomy(taxonomy or {})
        self._on_feedback = on_feedback
        self._store = make_store(self.cfg.store_path)
        self._rng = random.Random(self.cfg.seed)
        self._lock = threading.Lock()
        # per-user volatile memory: recent turns
        self._recent_cats: Dict[str, List[str]] = {}      # last N categories
        self._recent_items: Dict[str, List[str]] = {}     # dedupe window
        self._lists: Dict[str, List[List[str]]] = {}      # for intralist diversity
        self._audit: List[DecisionRecord] = []
        self._turn: Dict[str, int] = {}
        self._used_tokens: set = set()
        for it in items or []:
            self.register(it)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, item: Item) -> None:
        with self._lock:
            self._items[item.id] = item

    def register_all(self, items: List[Item]) -> None:
        for it in items:
            self.register(it)

    def set_adjacency(self, adjacency: Dict[str, List[Tuple[str, float]]]) -> None:
        """Host refreshes the walk graph (ICE adapter calls this every
        few turns from the live CuriosityGraph)."""
        with self._lock:
            self._adjacency = dict(adjacency or {})

    def set_taxonomy(self, parents: Dict[str, str]) -> None:
        with self._lock:
            self._taxonomy = Taxonomy(parents or {})

    # ------------------------------------------------------------------
    # suggest
    # ------------------------------------------------------------------
    def suggest(
        self,
        user: str,
        n: int = 3,
        context_tags: Sequence[str] = (),
        context_embedding: Optional[List[float]] = None,
        exclude: Sequence[str] = (),
    ) -> List[Suggestion]:
        if n <= 0:
            return []
        with self._lock:
            turn = self._turn.get(user, 0) + 1
            self._turn[user] = turn

            eligible = [
                it for it in self._items.values()
                if self._passes_safety(it, slot=None)
                and it.id not in set(exclude)
            ]
            if not eligible:
                return []

            events = self._store.events(user)
            engaged_tags = [
                self._items[e["item"]].tags
                for e in events if e["signal"] == "engaged" and e["item"] in self._items
            ]
            user_top_tags = interest_tags(events, lambda i: self._items.get(i, Item(i, "")).tags, top=3)
            user_top_cats = []
            seen_cats = set()
            for e in events:
                if e["signal"] == "engaged" and e["item"] in self._items:
                    c = self._items[e["item"]].category
                    if c and c not in seen_cats:
                        seen_cats.add(c)
                        user_top_cats.append(c)

            # router weights for this call
            stats = {s: self._store.slot_counts(user, s) for s in SLOTS}
            weights = effective_weights(
                self.cfg.weights, stats, self.cfg.adaptive_router,
                self.cfg.adaptive_min_shown, self.cfg.adaptive_floor,
                self.cfg.exploit_floor, self.cfg.entropy_ceiling,
            )
            slots = draw_slots(n, weights, self._rng)

            recent_cats = list(self._recent_cats.get(user, []))[-self.cfg.recent_window:]
            recent_items = list(self._recent_items.get(user, []))[-self.cfg.dedupe_window:]
            already_picked: List[Item] = []
            out: List[Suggestion] = []
            suppressed: List[str] = []
            considered: Dict[str, int] = {}
            reasons: Dict[str, str] = {}

            for slot in slots:
                item, reason, scores = self._pick(
                    user, slot, eligible, context_tags, context_embedding,
                    recent_cats, recent_items, already_picked,
                    engaged_tags, user_top_tags, user_top_cats, suppressed,
                )
                if item is None:
                    # slot exhausted -> fallback chain, never return empty
                    for fb in FALLBACK_ORDER:
                        if fb == slot:
                            continue
                        item, reason, scores = self._pick(
                            user, fb, eligible, context_tags, context_embedding,
                            recent_cats, recent_items, already_picked,
                            engaged_tags, user_top_tags, user_top_cats, suppressed,
                        )
                        if item is not None:
                            slot = fb
                            break
                if item is None:
                    continue    # truly nothing left this turn

                token = uuid.uuid4().hex[:16]
                sug = Suggestion(
                    token=token, item=item, slot=slot, reason=reason,
                    scores=scores, turn=turn,
                )
                out.append(sug)
                already_picked.append(item)
                considered[slot] = considered.get(slot, 0) + 1
                reasons[item.id] = reason

                # bookkeeping
                self._store.put_token(token, user, item.id, slot, turn)
                self._store.bump_slot(user, slot, shown=1)
                eff_shown, eff_engaged = self._effective_global(item)
                self._store.bump_global(item.id, shown=1)
                self._store.log_event(user, item.id, slot, "shown")
                if item.category:
                    rc = self._recent_cats.setdefault(user, [])
                    rc.append(item.category)
                    del rc[:-self.cfg.recent_window]
                ri = self._recent_items.setdefault(user, [])
                ri.append(item.id)
                del ri[:-self.cfg.dedupe_window]

            self._lists.setdefault(user, []).append([s.item.id for s in out])

            if self.cfg.audit:
                self._audit.append(
                    DecisionRecord(
                        turn=turn, user=user, slots_drawn=slots,
                        candidates_considered=considered,
                        suppressed=suppressed,
                        final=[s.item.id for s in out],
                        reasons=reasons,
                    )
                )
            return out

    # ------------------------------------------------------------------
    # the four slot pools
    # ------------------------------------------------------------------
    def _pick(
        self, user, slot, eligible, context_tags, context_embedding,
        recent_cats, recent_items, already_picked,
        engaged_tags, user_top_tags, user_top_cats, suppressed,
    ) -> Tuple[Optional[Item], str, Dict[str, float]]:
        ranked = self._slot_pool(
            user, slot, eligible, context_tags, context_embedding,
            engaged_tags, user_top_tags, user_top_cats,
        )
        if not ranked:
            return None, "", {}
        # Apply the configured category controls before MMR. Repeated
        # categories remain available only as a last resort, so a sparse
        # catalog still produces a useful suggestion.
        ranked = [
            (iid, score * decay_multiplier(
                self._items[iid].category, recent_cats, self.cfg.decay_base
            ) * (
                self.cfg.reroll_penalty
                if reroll_needed(self._items[iid].category, recent_cats)
                else 1.0
            ), reason)
            for iid, score, reason in ranked
        ]
        # MMR ordering for this pool (research formula; lambda dial)
        relevance = {iid: sc for iid, sc, _r in ranked}
        ordered = mmr_select(
            [iid for iid, _sc, _r in ranked], relevance,
            self._similarity_fn, k=len(ranked), lambda_mult=self.cfg.mmr_lambda,
        )
        deferred_rerolls = []
        for iid in ordered:
            item = self._items[iid]
            if item.id in recent_items:
                suppressed.append(f"{iid}:dedupe")
                continue
            if any(item.id == p.id for p in already_picked):
                suppressed.append(f"{iid}:batch")
                continue
            # within-batch near-duplicate skip
            if any(
                self._similarity_fn(iid, p.id) > 0.75 for p in already_picked
            ):
                suppressed.append(f"{iid}:mmr")
                continue
            # the hard re-roll rule: same category as last 2 turns
            if reroll_needed(item.category, recent_cats):
                suppressed.append(f"{iid}:reroll")
                deferred_rerolls.append(iid)
                continue
            reason = next(r for i, s, r in ranked if i == iid)
            scores = {iid: relevance[iid]}
            scores["decay"] = decay_multiplier(
                item.category, recent_cats, self.cfg.decay_base
            )
            return item, reason, scores
        # If every viable item is in a hot category, honor the configured
        # penalty rather than returning an empty slot.
        for iid in deferred_rerolls:
            item = self._items[iid]
            if item.id in recent_items or any(item.id == p.id for p in already_picked):
                continue
            reason = next(r for i, _s, r in ranked if i == iid)
            return item, reason, {
                iid: relevance[iid],
                "decay": decay_multiplier(item.category, recent_cats, self.cfg.decay_base),
                "reroll_penalty": self.cfg.reroll_penalty,
            }
        return None, "", {}

    def _slot_pool(
        self, user, slot, eligible, context_tags, context_embedding,
        engaged_tags, user_top_tags, user_top_cats,
    ) -> List[Tuple[str, float, str]]:
        """[(item_id, score, reason)] sorted best-first, safety pre-applied."""
        pool = [it for it in eligible if self._passes_safety(it, slot=slot)]
        if not pool:
            return []

        if slot == "exploit":
            out = []
            for it in pool:
                pm = posterior_mean(
                    *self._store.arm(user, it.id),
                    self.cfg.prior_alpha, self.cfg.prior_beta,
                )
                if context_tags:
                    aff = tag_jaccard(it.tags, context_tags)
                elif context_embedding is not None and it.embedding is not None:
                    from .diversity import cosine
                    aff = max(0.0, cosine(it.embedding, context_embedding))
                else:
                    aff = 0.0
                rel = 0.6 * pm + 0.4 * aff
                out.append((it.id, rel, "Close to what you already like"))
            out.sort(key=lambda x: (-x[1], x[0]))
            return out

        if slot == "adjacent":
            out = []
            # 1) taxonomy stepping: siblings/cousins of engaged categories
            adj_cats: List[str] = []
            for cat in user_top_cats:
                adj_cats.extend(self._taxonomy.adjacent_categories(cat))
            adj_set = set(adj_cats)
            # 2) walk results over the host graph, seeded at engaged items
            seed_ids = [
                e["item"] for e in self._store.events(user)
                if e["signal"] == "engaged" and e["item"] in self._items
            ][-3:]
            walk_scores: Dict[str, float] = {}
            if self._adjacency and seed_ids:
                walk_scores = biased_random_walk(
                    self._adjacency, seed_ids,
                    self.cfg.walk_length, self._rng,
                    walks_per_seed=self.cfg.walk_walks_per_seed,
                    restart_prob=self.cfg.walk_restarts,
                )
            # 3) multimodal bridges
            bridge_ids = set(
                multimodal_bridges(
                    [(it.id, it.title, it.tags) for it in pool], user_top_tags
                )
            )
            for it in pool:
                reason, base = "A sideways step", 0.5
                if it.id in bridge_ids:
                    reason = f"Bridges your {' + '.join(sorted(set(user_top_tags) & set(it.tags)))}"
                    base = 0.75
                elif walk_scores.get(it.id, 0.0) > 0:
                    base = 0.55 + 0.45 * walk_scores[it.id]
                    reason = f"Walked L={self.cfg.walk_length} from what you like"
                elif it.category in adj_set:
                    base = 0.65
                    sibs = ", ".join(self._taxonomy.siblings(it.category)[:2]) or "neighbors"
                    reason = f"One step from {self._taxonomy.parent(it.category) or 'your topics'} ({sibs})"
                elif not user_top_cats and not walk_scores:
                    # cold user: no history to step from — nearest-context items
                    if context_tags and tag_jaccard(it.tags, context_tags) > 0:
                        base = 0.55
                        reason = "Near your current conversation"
                out.append((it.id, base, reason))
            out.sort(key=lambda x: (-x[1], x[0]))
            return out

        if slot == "uncertainty":
            def get_global(iid: str):
                return self._effective_global(self._items[iid])
            scores = score_all(
                [it.id for it in pool], get_global, self.cfg.policy,
                self.cfg.ucb_c, self._rng, self.cfg.prior_alpha, self.cfg.prior_beta,
            )
            out = []
            for it in pool:
                shown, _eng = self._effective_global(it)
                reason = (
                    f"Barely shown anywhere (n={shown} globally) — "
                    "uncertainty spike, testing the waters"
                )
                out.append((it.id, scores[it.id], reason))
            out.sort(key=lambda x: (-x[1], x[0]))
            return out

        # entropy
        shown_map = {it.id: self._effective_global(it)[0] for it in pool}
        ordered = sorted(pool, key=lambda i: (shown_map[i.id], i.id))
        keep = max(1, int(len(ordered) * self.cfg.entropy_pool_fraction))
        bottom = ordered[:keep]
        if not bottom:
            return []
        picked = self._rng.choice(bottom)
        return [(
            picked.id, 1.0,
            "A wildcard from the least-seen pile (quality-checked)",
        )]
    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _effective_global(self, item: Item) -> Tuple[int, int]:
        s, e = self._store.global_counts(item.id)
        return s + item.n_shown_global, e

    def _passes_safety(self, item: Item, slot: Optional[str]) -> bool:
        if item.quality < self.cfg.min_quality:
            return False
        if item.category in self.cfg.blocked_categories:
            return False
        if any(t in self.cfg.blocked_tags for t in item.tags):
            return False
        if slot == "entropy" and item.quality < self.cfg.entropy_min_quality:
            return False
        return True

    def _similarity_fn(self, a: str, b: str) -> float:
        ia, ib = self._items.get(a), self._items.get(b)
        if ia is None or ib is None:
            return 0.0
        return default_similarity(ia.tags, ia.embedding, ib.tags, ib.embedding)

    # ------------------------------------------------------------------
    # record
    # ------------------------------------------------------------------
    def record(self, token: str, signal: str) -> dict:
        if signal not in (
            FeedbackSignal.ENGAGED, FeedbackSignal.IGNORED, FeedbackSignal.NEGATIVE
        ):
            raise ValueError(f"unknown signal: {signal}")
        with self._lock:
            info = self._store.get_token(token)
            if info is None:
                raise ValueError("unknown token (from another engine instance or wiped store)")
            if not self._store.consume_token(token):
                raise ValueError("token already recorded (one reaction per suggestion)")
            user, item_id, slot, _turn = info
            item = self._items.get(item_id)

        if signal == FeedbackSignal.ENGAGED:
            self._store.bump_arm(user, item_id, alpha=1)
            self._store.bump_global(item_id, engaged=1)
            self._store.bump_slot(user, slot, engaged=1)
        elif signal == FeedbackSignal.IGNORED:
            self._store.bump_arm(user, item_id, beta=1)
        else:
            self._store.bump_arm(user, item_id, beta=int(self.cfg.negative_beta_weight))
        self._store.log_event(user, item_id, slot, signal)
        if self._on_feedback and item is not None:
            try:
                self._on_feedback(item, slot, signal)
            except Exception:
                pass
        a, b = self._store.arm(user, item_id)
        return {"item": item_id, "slot": slot, "signal": signal, "alpha": a, "beta": b}

    def close(self) -> None:
        """Release durable resources held by the engine."""
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.close()

    # ------------------------------------------------------------------
    # metrics + audit
    # ------------------------------------------------------------------
    def metrics(self, user: Optional[str] = None) -> dict:
        events = self._store.events(user)
        shown_counts: Dict[str, int] = {}
        for e in events:
            if e["signal"] == "shown":
                shown_counts[e["item"]] = shown_counts.get(e["item"], 0) + 1
        catalog = list(self._items.keys())
        rep = exposure_report(catalog, shown_counts)
        rep["slots"] = slot_report(events)
        rep["serendipity_rate"] = serendipity_rate(events)
        lists = self._lists.get(user or "", [])[-10:]
        divs = [
            intralist_diversity(lambda i: self._items.get(i, Item(i, "")).tags, lst)
            for lst in lists
        ]
        divs = [d for d in divs if d is not None]
        rep["intralist_diversity"] = round(sum(divs) / len(divs), 4) if divs else None
        return rep

    def decision_log(self) -> List[DecisionRecord]:
        return list(self._audit)
