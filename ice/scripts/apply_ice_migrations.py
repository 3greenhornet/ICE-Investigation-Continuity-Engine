#!/usr/bin/env python3
"""scripts/apply_ice_migrations.py — apply ICE migrations 012+ to a live DB.

Resolves the docker-compose known-unknown: the Postgres entrypoint only
executes SQL files directly under /docker-entrypoint-initdb.d, NOT files in
subdirectories — so mounting db/migrations there does not apply anything.
docker-compose.yml therefore runs this script in the api service before
uvicorn starts (spec Part 4 §19.6 acceptance criterion 5: "Migrations run
automatically on first startup").

Behavior:
    - Connects via DATABASE_URL (db.connection.get_pool).
    - Maintains an ``ice_migration_history`` table (filename PRIMARY KEY,
      applied_at). Merge-spec Part 1 §5.8 P8 follow-up: this records every
      file this runner has applied so that files WITHOUT a CREATE TABLE
      statement (012a_safety_check.sql and the Part 2 ALTER-only migrations
      015_external_analysis_and_case_type.sql /
      016_contradiction_uniqueness.sql) are applied exactly once instead of
      being skipped forever. 017_proposed_edges.sql has a CREATE TABLE and
      is covered by both the table-exists check and the history record.
    - For every db/migrations/*.sql file numbered >= 012, in
      _migration_sort_key order (letter-suffixed patches such as 012a run
      immediately BEFORE their base number: 011 -> 012a -> 012 -> 013):
        * skip if the file's first ``CREATE TABLE <name>`` already exists
          (information_schema.tables) — pre-history databases stay quiet;
        * skip if the filename is already in ice_migration_history;
        * otherwise execute the whole file, print APPLIED, and record it.
    - Files 002-011 are legacy migrations with their own runner (see
      scripts/migrate_all.py for the legacy inline schema) and are not
      touched here.
    - Exits 0 even when everything is skipped (idempotent); exits 1 on a
      hard failure so the container start sequence surfaces it.

Usage:
    python scripts/apply_ice_migrations.py [--migrations-dir DIR]
"""

from __future__ import annotations
import argparse
import asyncio
import re
import sys
from pathlib import Path

# Allow running as a plain script from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import get_pool  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"

_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)", re.I)
# ICE migrations: 012-019, 020-099, 100-199. Letter-suffixed patch files
# (012a_safety_check.sql) share the numeric prefix and match too.
_FILE_RE = re.compile(r"^(0(1[2-9]|[2-9][0-9])|1[0-9][0-9])")


def first_table(sql_text: str) -> str | None:
    match = _TABLE_RE.search(sql_text)
    return match.group(1) if match else None


def _migration_sort_key(name: str) -> tuple:
    """Order migrations so letter-suffixed patches precede their base file.

    Lexicographic ordering would place "012a_safety_check.sql" AFTER
    "012_case_model.sql" ('a' = 0x61 > '_' = 0x5F), which would run the
    012a safety check AFTER migration 012 has already dropped the legacy
    tables — defeating its purpose. This key treats "012a" as 12.0 and
    "012_case_model" as 12.1: split the leading numeric prefix, then rank
    an immediate letter suffix (a pre-patch) before an underscore/plain
    tail. Examples:
        011_edge_weights.sql   -> (11, 1, "_edge_weights.sql")
        012a_safety_check.sql  -> (12, 0, "a_safety_check.sql")
        012_case_model.sql     -> (12, 1, "_case_model.sql")
        013_evidence_graph.sql -> (13, 1, "_evidence_graph.sql")
        014_case_events.sql    -> (14, 1, "_case_events.sql")
    """
    match = re.match(r"^(\d+)", name)
    if not match:
        # No numeric prefix: sort after every numbered migration, by name.
        return (float("inf"), 1, name)
    num = int(match.group(1))
    rest = name[len(match.group(1)):]
    if rest and rest[0].isalpha():
        return (num, 0, rest)
    return (num, 1, rest)


async def table_exists(conn, table: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = $1
        """,
        table,
    )
    return row is not None


async def applied_files(conn) -> set[str]:
    """Return the filenames recorded in ice_migration_history (creates it)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ice_migration_history (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    rows = await conn.fetch("SELECT filename FROM ice_migration_history")
    return {r["filename"] for r in rows}


async def record_applied(conn, filename: str) -> None:
    await conn.execute(
        """
        INSERT INTO ice_migration_history (filename)
        VALUES ($1)
        ON CONFLICT (filename) DO NOTHING
        """,
        filename,
    )


async def main(migrations_dir: Path) -> int:
    files = sorted(
        (p for p in migrations_dir.glob("*.sql") if _FILE_RE.match(p.name)),
        key=lambda p: _migration_sort_key(p.name),
    )
    if not files:
        print(f"apply_ice_migrations: no ICE migration files in {migrations_dir}")
        return 0

    pool = await get_pool()
    if pool is None:
        print(
            "apply_ice_migrations: DATABASE_URL not set or Postgres "
            "unreachable — cannot apply migrations.",
            file=sys.stderr,
        )
        return 1

    applied = skipped = 0
    try:
        async with pool.acquire() as conn:
            history = await applied_files(conn)
            for path in files:
                sql_text = path.read_text(encoding="utf-8")
                table = first_table(sql_text)
                if table is not None and await table_exists(conn, table):
                    print(f"  SKIP {path.name}: table '{table}' already exists")
                    skipped += 1
                    continue
                if path.name in history:
                    reason = (
                        f"table '{table}' missing" if table is not None
                        else "no CREATE TABLE (history-recorded)"
                    )
                    print(f"  SKIP {path.name}: already applied ({reason})")
                    skipped += 1
                    continue
                try:
                    await conn.execute(sql_text)
                    await record_applied(conn, path.name)
                    history.add(path.name)
                    label = f"table '{table}'" if table is not None else "no CREATE TABLE"
                    print(f"  APPLIED {path.name} ({label})")
                    applied += 1
                except Exception as exc:
                    print(
                        f"  FAILED {path.name}: {exc}",
                        file=sys.stderr,
                    )
                    return 1
    finally:
        await pool.close()

    print(
        f"apply_ice_migrations: done — {applied} applied, {skipped} skipped, "
        f"{len(files)} total."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=DEFAULT_DIR,
        help="directory containing ICE .sql migrations (default: db/migrations)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.migrations_dir)))