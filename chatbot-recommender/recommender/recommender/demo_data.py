"""Seeded synthetic catalog for demos and tests.

Deliberately contains the user's own examples so the mechanisms are
demonstrated on exactly the cases they were designed for:

* old videos with ~100 plays (the long tail),
* no-name new authors (<150 followers),
* topics unrelated to anything (stamp collecting, metro map design),
* plus enough famous/viral content that a boring mirror-the-user bot
  would only ever surface the popular stuff.
"""

from __future__ import annotations

import random

from .catalog import Author, Catalog, Item

# Category neighbours (undirected). Weights: 0.9 strong, 0.5 medium, 0.25 weak.
CATEGORY_EDGES: list[tuple[str, str, float]] = [
    # visual-arts cluster -- "see paintings -> recommend sculpture"
    ("painting", "sculpture", 0.9),
    ("painting", "photography", 0.9),
    ("painting", "architecture", 0.5),
    ("sculpture", "architecture", 0.9),
    ("sculpture", "photography", 0.5),
    ("photography", "architecture", 0.9),
    # knowledge cluster
    ("documentary", "science", 0.9),
    ("coding", "science", 0.5),
    ("coding", "documentary", 0.25),
    ("music_video", "documentary", 0.5),
    ("music_video", "photography", 0.25),
    # everyday-life cluster
    ("cooking", "documentary", 0.25),
    ("cooking", "gardening", 0.5),
    ("gardening", "science", 0.25),
    # deliberately lonely categories (orthogonal-topic fodder)
    ("philately", "documentary", 0.25),
    ("metro_maps", "architecture", 0.5),
    ("metro_maps", "design_history", 0.9),
    ("design_history", "architecture", 0.5),
    ("design_history", "photography", 0.25),
]

_FAMOUS_AUTHORS = [
    ("Studio Meridian", 410_000),
    ("The Open Archive", 96_000),
    ("Kanal Wissen", 220_000),
    ("Modern Craft TV", 155_000),
    ("Field Notes Lab", 64_000),
]
_MID_AUTHORS = [
    ("Two Desk Studio", 9_400),
    ("Quiet Signal", 3_100),
    ("Harbor Workshop", 7_700),
    ("Backroom Essays", 1_900),
    ("Lamplight Media", 12_000),
]
_UNKNOWN_AUTHORS = [
    ("mira_doodles", 41),
    ("old_tape_vault", 12),
    ("a_nobody_named_kim", 87),
    ("garage.essays", 63),
    ("postsfromtheattic", 8),
    ("silent_reel_2009", 22),
]

_SEED_TITLES: dict[str, list[tuple[str, str]]] = {
    # category -> [(title, topics)]
    "painting": [
        ("Understanding Light in Oil Painting", ("painting", "technique", "light")),
        ("Watercolor Wet-on-Wet Basics", ("painting", "watercolor", "tutorial")),
        ("Vermeer and the Camera Obscura", ("painting", "history", "optics")),
        ("Plein Air Sketching in One Hour", ("painting", "outdoors", "sketching")),
        ("Color Theory That Actually Sticks", ("painting", "color", "theory")),
        ("Restoring a 19th Century Portrait", ("painting", "restoration", "craft")),
        ("Impressionism: Painting Modern Life", ("painting", "impressionism", "history")),
        ("Gesso, Grounds and Patience", ("painting", "materials", "craft")),
    ],
    "sculpture": [
        ("Marble Carving: From Block to Form", ("sculpture", "marble", "technique")),
        ("Clay Modelling for Beginners", ("sculpture", "clay", "tutorial")),
        ("Bronze Casting in Three Steps", ("sculpture", "bronze", "foundry")),
        ("Rodin's Hands Studies", ("sculpture", "rodin", "history")),
        ("Wire Armatures That Hold", ("sculpture", "wire", "craft")),
        ("Public Sculpture and Public Space", ("sculpture", "urban", "public art")),
        ("Woodcarving with Three Chisels", ("sculpture", "wood", "technique")),
        ("Assemblage: Sculpture From Scrap", ("sculpture", "assemblage", "recycling")),
    ],
    "photography": [
        ("Street Photography Without Creeping People Out", ("photography", "street", "ethics")),
        ("Large Format on a Budget", ("photography", "large format", "gear")),
        ("Understanding the Zone System", ("photography", "exposure", "technique")),
        ("Golden Hour Is Not the Only Hour", ("photography", "light", "composition")),
        ("Darkroom Printing at Home", ("photography", "darkroom", "film")),
        ("Color Grading Photographs", ("photography", "color", "post-processing")),
        ("Documentary Projects That Take Years", ("photography", "documentary", "longform")),
    ],
    "architecture": [
        ("Reading a Floor Plan Fluently", ("architecture", "drawing", "literacy")),
        ("Brutalism: Concrete and Controversy", ("architecture", "brutalism", "history")),
        ("How Arches Actually Work", ("architecture", "structure", "engineering")),
        ("Vernacular Architecture of Cold Climates", ("architecture", "vernacular", "climate")),
        ("Adaptive Reuse: Church to Library", ("architecture", "reuse", "urban")),
        ("Sketching Buildings on Location", ("architecture", "sketching", "urban")),
        ("The Elevator Changed Everything", ("architecture", "history", "cities")),
    ],
    "documentary": [
        ("The Men Who Maintained Lighthouses", ("documentary", "history", "labor")),
        ("Inside a Seed Vault", ("documentary", "science", "food security")),
        ("Last Speakers of a Dying Language", ("documentary", "language", "anthropology")),
        ("A Year at the Salvage Yard", ("documentary", "labor", "recycling")),
        ("The Maps That Lied", ("documentary", "cartography", "history")),
        ("Night Trains of Europe", ("documentary", "railways", "travel")),
    ],
    "science": [
        ("Why Ice Floats: A Deep Dive", ("science", "physics", "chemistry")),
        ("The Slowest Experiments in Science", ("science", "longterm", "method")),
        ("How Birds Navigate", ("science", "biology", "behavior")),
        ("Standard Time and Its Discontents", ("science", "time", "history")),
        ("Soil Is More Alive Than You Think", ("science", "biology", "soil")),
        ("Measuring a Planet With a Stick", ("science", "astronomy", "history")),
    ],
    "coding": [
        ("Build a CLI in 15 Minutes", ("coding", "python", "tutorial")),
        ("Read a Codebase Like a Novel", ("coding", "reading", "practice")),
        ("The Test That Found Nothing", ("coding", "testing", "practice")),
        ("SQLite Is Enough for Most Things", ("coding", "sqlite", "architecture")),
        ("Debugging by Bisection", ("coding", "debugging", "method")),
        ("A Tiny Interpreter in 200 Lines", ("coding", "interpreters", "craft")),
    ],
    "music_video": [
        ("One-Take Music Videos", ("music video", "direction", "craft")),
        ("How Album Covers Got Weird", ("music video", "design", "history")),
        ("Live From a Rooftop", ("music video", "live", "urban")),
        ("Synesthesia in Visual Music", ("music video", "art", "perception")),
        ("The Anatomy of a Drop", ("music video", "editing", "rhythm")),
    ],
    "cooking": [
        ("One-Pan Ramen at Home", ("cooking", "ramen", "home cooking")),
        ("Bread Science: Hydration Explained", ("cooking", "baking", "science")),
        ("Grandmother's Recipe, Written Down", ("cooking", "family", "tradition")),
        ("Knife Skills in Ten Minutes", ("cooking", "technique", "basics")),
        ("Fermentation You Already Do", ("cooking", "fermentation", "science")),
    ],
    "gardening": [
        ("A Balcony Garden in Year One", ("gardening", "balcony", "beginner")),
        ("Composting Without the Smell", ("gardening", "compost", "soil")),
        ("Saving Seeds Like It's 1900", ("gardening", "seeds", "tradition")),
        ("Shade Gardening Under Trees", ("gardening", "shade", "planning")),
    ],
    "philately": [
        ("Stamp Errors Worth Fortunes", ("philately", "stamps", "collecting")),
        ("The Postal History of Nowhere", ("philately", "history", "postal")),
        ("Watermarks, Hinges and Patience", ("philately", "stamps", "craft")),
    ],
    "metro_maps": [
        ("Why Metro Maps Lie", ("metro maps", "cartography", "design")),
        ("Beck's London Diagram at 90", ("metro maps", "history", "london")),
        ("Designing a Transit Map From Scratch", ("metro maps", "design", "process")),
        ("Circular Line Maps of the World", ("metro maps", "cartography", "circles")),
    ],
    "design_history": [
        ("The Typography of Protest Signs", ("design", "typography", "politics")),
        ("Ballpoint Pens and Democracy", ("design", "objects", "history")),
        ("When Chairs Went Plastic", ("design", "furniture", "materials")),
        ("Instructional Diagrams Before ISO", ("design", "diagrams", "standards")),
    ],
}

_KIND_BY_CATEGORY = {
    "painting": "video",
    "sculpture": "video",
    "photography": "video",
    "architecture": "video",
    "documentary": "video",
    "science": "video",
    "coding": "video",
    "music_video": "video",
    "cooking": "video",
    "gardening": "article",
    "philately": "article",
    "metro_maps": "article",
    "design_history": "article",
}


def build_demo_catalog() -> Catalog:
    """Deterministic catalog: same numbers on every call (seeded RNG)."""
    rng = random.Random(20260617)
    authors: list[Author] = []
    for index, (name, followers) in enumerate(_FAMOUS_AUTHORS + _MID_AUTHORS + _UNKNOWN_AUTHORS):
        authors.append(Author(id=f"author-{index:02d}", name=name, followers=followers))
    famous = authors[: len(_FAMOUS_AUTHORS)]
    mid = authors[len(_FAMOUS_AUTHORS): len(_FAMOUS_AUTHORS) + len(_MID_AUTHORS)]
    unknown = authors[len(_FAMOUS_AUTHORS) + len(_MID_AUTHORS):]

    items: list[Item] = []
    counter = 0

    def add(title: str, category: str, topics: tuple[str, ...], author: Author,
            play_count: int, age_days: int) -> None:
        nonlocal counter
        item_id = f"item-{counter:04d}"
        counter += 1
        items.append(
            Item(
                id=item_id,
                title=title,
                kind=_KIND_BY_CATEGORY[category],
                category=category,
                author=author,
                topics=topics,
                description=(
                    f"A {category.replace('_', ' ')} piece about "
                    f"{', '.join(topics[:2])}."
                ),
                published_days_ago=age_days,
                play_count=play_count,
            )
        )

    for category, seeds in _SEED_TITLES.items():
        for title, topics in seeds:
            popularity_bucket = rng.random()
            if popularity_bucket < 0.35:
                # viral-ish: famous author, high plays, recent
                author = rng.choice(famous)
                plays = rng.randint(180_000, 2_400_000)
                age = rng.randint(5, 400)
            elif popularity_bucket < 0.70:
                # mid tier
                author = rng.choice(mid)
                plays = rng.randint(2_500, 60_000)
                age = rng.randint(60, 1_400)
            else:
                # the long tail: unknown author, few plays, old
                author = rng.choice(unknown)
                plays = rng.randint(15, 780)
                age = rng.randint(400, 5_400)
            add(title, category, topics, author, plays, age)

        # extra long-tail variants per category (more obscure versions of
        # the same theme -- the "old video with 100 plays" class)
        for extra_index in range(2):
            base_title, base_topics = seeds[extra_index % len(seeds)]
            year = rng.randint(2008, 2014)
            add(
                f"{base_title} ({year} revisited)",
                category,
                base_topics,
                rng.choice(unknown),
                play_count=rng.randint(40, 240),
                age_days=rng.randint(3_800, 6_200),
            )

    # orthogonal-topic showcase: things adjacent to NOTHING the user liked
    add(
        "Silent Reel 2009: A Town's Only Tape",
        "documentary",
        ("documentary", "local history", "found footage"),
        unknown[5],
        play_count=98,
        age_days=5_112,
    )
    add(
        "Postmarks From a Vanished Country",
        "philately",
        ("philately", "stamps", "history"),
        unknown[2],
        play_count=112,
        age_days=4_700,
    )

    return Catalog(items)
