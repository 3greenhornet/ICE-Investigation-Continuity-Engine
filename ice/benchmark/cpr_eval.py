"""Cross-field retrieval benchmark on CPR-eval (non-circular).

Dataset: Cross-Pollination Retrieval (Jung et al., arXiv:2610.25678), ODC-BY,
https://huggingface.co/datasets/CPR-Dataset/CPR-Dataset. Each query states a
research problem; among 151 candidates exactly one paper from ANOTHER topic
holds the transferable idea. Download eval/eval.jsonl and eval/papers.jsonl
into --data (default <workspace>/.cache/datasets/cpr).

Only abstracts under CC-BY/CC0 ship with the dataset, so this harness uses the
queries whose positive has an abstract and ranks only candidates that have
one. Scores are therefore NOT comparable to the paper's table, but every
method below faces exactly the same task, so the comparison between methods
is fair. The random baseline is reported per subset.

Methods (all rank title + abstract):
  random      expected value of a random order
  tfidf       TF-IDF cosine (query vs candidates)
  bge         BAAI/bge-base-en-v1.5 cosine (ICE's local embedder)
  hybrid      mean of min-max normalised tfidf and bge
  strip_*     the query first reduced to a domain-stripped computational
              fingerprint (case_engine.fingerprint, one LLM call per query),
              then tfidf / bge / hybrid  [--llm N: first N queries only]

    python benchmark/cpr_eval.py                 # non-LLM methods, all usable queries
    python benchmark/cpr_eval.py --llm 40        # + fingerprinted queries on 40 of them
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

ICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ICE))
DEFAULT_DATA = ICE.parents[1] / ".cache" / "datasets" / "cpr"


def load(data: Path):
    papers = {}
    with open(data / "papers.jsonl", encoding="utf-8") as f:
        for ln in f:
            p = json.loads(ln)
            if p.get("abstract"):
                papers[p["corpusid"]] = f"{p.get('title') or ''}. {p['abstract'][:2000]}"
    queries = []
    with open(data / "eval.jsonl", encoding="utf-8") as f:
        for ln in f:
            q = json.loads(ln)
            pos = q["positive"]["corpusid"]
            if pos not in papers:
                continue
            negs = [n["corpusid"] for n in q["negatives"] if n["corpusid"] in papers]
            if len(negs) < 10:
                continue
            queries.append({"id": q["query_id"], "query": q["query"], "field": q["field"],
                            "cands": [pos] + negs, "pos": pos})
    return papers, queries


def metrics(rank_of_pos: list[int], sizes: list[int]) -> dict:
    r = np.array(rank_of_pos)
    return {"n": len(r), "recall@1": round(float(np.mean(r < 1)), 4), "recall@5": round(float(np.mean(r < 5)), 4),
            "recall@10": round(float(np.mean(r < 10)), 4),
            "mrr@10": round(float(np.mean([1.0 / (x + 1) if x < 10 else 0.0 for x in r])), 4),
            "random_recall@10": round(float(np.mean([min(10, s) / s for s in sizes])), 4)}


def tfidf_scores(query: str, texts: list[str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2))
    m = vec.fit_transform([query] + texts)
    return cosine_similarity(m[0:1], m[1:]).ravel()


def _mm(x: np.ndarray) -> np.ndarray:
    span = x.max() - x.min()
    return (x - x.min()) / span if span > 0 else np.zeros_like(x)


class Embedder:
    PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, cache: Path, max_chars: int = 800):
        from engine.memory import embed_local_sync

        self.max_chars = max_chars  # bge sees ~200 tokens; TF-IDF still gets the full text
        self.embed = embed_local_sync
        self.cache_path = cache
        self.cache: dict[str, np.ndarray] = {}
        if cache.is_file():
            z = np.load(cache, allow_pickle=True)
            self.cache = dict(zip(z["keys"].tolist(), z["vecs"]))

    def docs(self, ids: list[int], papers: dict) -> np.ndarray:
        missing = [i for i in ids if str(i) not in self.cache]
        for k in range(0, len(missing), 64):
            chunk = missing[k:k + 64]
            for i, v in zip(chunk, self.embed([papers[i][:self.max_chars] for i in chunk])):
                self.cache[str(i)] = np.asarray(v, dtype=np.float32)
        return np.stack([self.cache[str(i)] for i in ids])

    def query(self, text: str) -> np.ndarray:
        return np.asarray(self.embed([self.PREFIX + text])[0], dtype=np.float32)

    def save(self) -> None:
        keys = list(self.cache)
        np.savez(self.cache_path, keys=np.array(keys, dtype=object), vecs=np.stack([self.cache[k] for k in keys]))


def rank_of(scores: np.ndarray) -> int:
    # candidate 0 is the positive; ties are broken against it (pessimistic)
    return int(np.sum(scores >= scores[0]) - 1)


async def strip_queries(qs: list[dict]) -> dict[str, str]:
    from case_engine.fingerprint import fingerprint_problem

    out = {}
    for q in qs:
        fp = await fingerprint_problem(q["query"])
        if fp and fp.get("MECHANISM"):
            out[q["id"]] = fp["MECHANISM"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--limit", type=int, default=80, help="sample size (0 = all usable queries; "
                    "bge on a laptop CPU embeds ~1-2 abstracts/s, so all 489 take hours)")
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--llm", type=int, default=0, help="also run domain-stripped queries on N queries")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    data = Path(args.data)
    papers, queries = load(data)
    random.Random(args.seed).shuffle(queries)
    if args.limit:
        queries = queries[:args.limit]
    print(f"usable queries: {len(queries)}; papers with abstracts: {len(papers)}", flush=True)

    emb = Embedder(data / f"bge_cache_{args.max_chars}.npz", args.max_chars)
    t0 = time.monotonic()
    ranks = {m: [] for m in ("tfidf", "bge", "hybrid")}
    sizes, per_q = [], {}
    for n, q in enumerate(queries, 1):
        texts = [papers[c] for c in q["cands"]]
        t = tfidf_scores(q["query"], texts)
        e = emb.docs(q["cands"], papers) @ emb.query(q["query"])
        h = 0.5 * _mm(t) + 0.5 * _mm(e)
        for m, s in (("tfidf", t), ("bge", e), ("hybrid", h)):
            ranks[m].append(rank_of(s))
        sizes.append(len(q["cands"]))
        per_q[q["id"]] = (t, e)
        if n % 50 == 0:
            print(f"  {n}/{len(queries)} queries, {time.monotonic() - t0:.0f}s", flush=True)
            emb.save()
    emb.save()
    results = {m: metrics(r, sizes) for m, r in ranks.items()}
    results["random"] = {"recall@10": metrics(ranks["tfidf"], sizes)["random_recall@10"]}

    if args.llm:
        sub = queries[:args.llm]
        stripped = asyncio.run(strip_queries(sub))
        sub = [q for q in sub if q["id"] in stripped]
        lr = {m: [] for m in ("tfidf", "bge", "hybrid", "strip_tfidf", "strip_bge", "strip_hybrid",
                              "strip+orig_hybrid")}
        lsizes = []
        for q in sub:
            texts = [papers[c] for c in q["cands"]]
            t, e = per_q[q["id"]]
            st = tfidf_scores(stripped[q["id"]], texts)
            se = emb.docs(q["cands"], papers) @ emb.query(stripped[q["id"]])
            for m, s in (("tfidf", t), ("bge", e), ("hybrid", 0.5 * _mm(t) + 0.5 * _mm(e)),
                         ("strip_tfidf", st), ("strip_bge", se), ("strip_hybrid", 0.5 * _mm(st) + 0.5 * _mm(se)),
                         ("strip+orig_hybrid", 0.25 * (_mm(t) + _mm(e) + _mm(st) + _mm(se)))):
                lr[m].append(rank_of(s))
            lsizes.append(len(q["cands"]))
        results["llm_subset"] = {m: metrics(r, lsizes) for m, r in lr.items()}
        results["llm_subset"]["examples"] = [{"query": q["query"], "stripped": stripped[q["id"]]} for q in sub[:3]]

    out = ICE / "benchmark" / "results" / f"cpr_eval_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dataset": "CPR-eval (abstract-available subset)", "queries": len(queries),
                               "results": results}, indent=1), encoding="utf-8")
    print(json.dumps(results, indent=1))
    print("written", out)


if __name__ == "__main__":
    main()
