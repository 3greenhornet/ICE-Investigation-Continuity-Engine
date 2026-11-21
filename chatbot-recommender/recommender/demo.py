"""Runnable demo: a scripted session that exercises all three mechanisms.

    python demo.py            # prints the transcript
    python demo.py --save     # also writes demo-transcript.txt
"""

from __future__ import annotations

import sys

from recommender import Chatbot, RecommenderConfig

SEPARATOR = "=" * 78


def header(title: str) -> None:
    print(f"\n{SEPARATOR}\n  {title}\n{SEPARATOR}")


def show_reply(reply) -> None:
    print(reply.text)


def main() -> None:
    lines: list[str] = []
    original_stdout = sys.stdout

    class Tee:
        def write(self, text: str) -> None:
            original_stdout.write(text)
            lines.append(text)

        def flush(self) -> None:
            original_stdout.flush()

    sys.stdout = Tee()  # type: ignore[assignment]

    try:
        header("THE DIVERSITY-FIRST CHATBOT RECOMMENDER -- live demo")
        print(
            "Three mechanisms, each backed by a real open-source engine:\n"
            "  1. similar-but-different : Qdrant retrieval + networkx category hops + MMR\n"
            "  2. wildcards             : Mab2Rec LinTS contextual bandit over 5 arms\n"
            "  3. anti-repetition       : hard seen-set + Hari semantic-refractory port\n"
        )

        bot = Chatbot(RecommenderConfig())
        report = bot.engine_report()
        print("Engines live for this session:")
        for slot, info in report.items():
            print(f"  {slot:9s} -> {info.get('implementation', info['engine'])}")
        print(f"  catalog   -> {len(bot.catalog.items)} items, "
              f"{len(bot.catalog.categories)} categories")

        # ── Turn 1: the boring-bot killer ────────────────────────────────
        header("TURN 1 -- user talks about paintings; bot does NOT mirror")
        reply = bot.chat(
            "I have been watching videos about oil painting and watercolor technique"
        )
        show_reply(reply)
        print(f"\n[mechanism check] focus={reply.feed.focus_category}, "
              f"arms={reply.feed.breakdown['arm_chosen']}, "
              f"exploit={reply.feed.breakdown['similar_but_different']}, "
              f"wildcard={reply.feed.breakdown['wildcard']}")

        # ── feedback on the wildcard (if any) ────────────────────────────
        wildcard_rec = next(
            (r for r in reply.recommendations if r.mechanism.startswith("wildcard:")),
            None,
        )
        if wildcard_rec is not None:
            print("\n[user feedback] liked the wildcard")
            bot.feedback(wildcard_rec.item.id, "liked")

        # ── Turn 2: different focus ──────────────────────────────────────
        header("TURN 2 -- user switches to coding; neighbourhood shifts")
        reply = bot.chat("python tutorials and building a CLI")
        show_reply(reply)
        print(f"\n[mechanism check] focus={reply.feed.focus_category}")

        # ── several more turns to show no-repetition + wildcard learning ─
        header("TURNS 3-7 -- more turns: nothing repeats, wildcards keep firing")
        for message in (
            "street photography and composition",
            "brutalist architecture",
            "documentaries about lighthouses",
            "bread baking science",
            "stamp collecting history",
        ):
            reply = bot.chat(message)
            wildcard = next(
                (r for r in reply.recommendations
                 if r.mechanism.startswith("wildcard:")),
                None,
            )
            marker = f"  [wildcard: {reply.feed.wildcard_arm}]" if wildcard else ""
            print(f"\n> {message}{marker}")
            for recommendation in reply.recommendations:
                tag = "W" if recommendation.mechanism.startswith("wildcard:") else " "
                print(
                    f" {tag} {recommendation.item.title} "
                    f"[{recommendation.item.category}] "
                    f"({recommendation.item.play_count:,} plays)"
                )
            # like everything this session (happy user) -- trains the bandit
            for recommendation in reply.recommendations:
                bot.feedback(recommendation.item.id, "liked")

        header("SESSION STATS -- what the mechanisms actually did")
        stats = bot.stats()
        print(f"turns completed      : {stats['turn']}")
        print(f"distinct items shown : {stats['seen_items']} (zero repeats, guaranteed)")
        print(f"category history     : {stats['history_categories']}")
        print(f"bandit engine        : {stats['bandit_engine']}")
        print("\nWildcard arm statistics (the bandit's learning state):")
        for arm, entry in stats["wildcard_arm_stats"].items():
            if entry["pulls"]:
                print(
                    f"  {arm:18s} pulls={entry['pulls']:.0f}  "
                    f"avg_reward={entry['avg_reward']:.2f}"
                )
            else:
                print(f"  {arm:18s} (never pulled)")
        print(
            "\nEvery recommendation above carries its reason; ask the bot "
            "'why' anytime. Swap engines via RecommenderConfig "
            "(bandit_engine='vowpal_wabbit', graph_engine='neo4j', ...) or "
            "point GORSE_ENDPOINT at a live Gorse server."
        )
    finally:
        sys.stdout = original_stdout

    if "--save" in sys.argv:
        with open("demo-transcript.txt", "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
        print("\n[saved] demo-transcript.txt")


if __name__ == "__main__":
    main()
