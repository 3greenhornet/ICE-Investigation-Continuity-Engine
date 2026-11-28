# hari/db/connection.py
import logging
import os
import asyncpg
from typing import Optional
from pgvector.asyncpg import register_vector

_pool: Optional[asyncpg.Pool] = None

# logging instead of emoji print(): print() of non-cp1252 characters raises
# UnicodeEncodeError on Windows when stdout is a pipe/file, which aborted
# the FastAPI lifespan before the server could start.
logger = logging.getLogger("ice.db")


async def init_db():
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.warning("DATABASE_URL not set - running without database")
        return
    try:
        if _pool is None:
            # The vector extension must exist BEFORE create_pool runs init=register_vector.
            # register_vector requires the 'vector' type to already be present in the
            # database, and asyncpg opens the first connection eagerly during pool
            # creation. So we bootstrap the extension on a plain connection first.
            bootstrap_conn = await asyncpg.connect(dsn)
            try:
                await bootstrap_conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            finally:
                await bootstrap_conn.close()

            _pool = await asyncpg.create_pool(
                dsn,
                # Pool size is configurable for load: DB_POOL_MIN / DB_POOL_MAX.
                min_size=int(os.getenv("DB_POOL_MIN", "1")),
                max_size=int(os.getenv("DB_POOL_MAX", "10")),
                init=register_vector,
                server_settings={"search_path": "public"}
            )
            logger.info("Database pool connected")

        # Verify the memories table is visible to this connection.
        async with _pool.acquire() as conn:
            table_exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'memories'
                );
            """)

            if not table_exists:
                logger.warning("Table 'memories' not found; initializing schema inline")
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        turn_number INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        event_type TEXT,
                        thematic_tags TEXT[],
                        significance FLOAT DEFAULT 0.5,
                        meaning_summary TEXT,
                        embedding vector(768),
                        created_at TIMESTAMP DEFAULT NOW(),
                        usage_count INTEGER DEFAULT 0,
                        last_retrieved_turn INTEGER DEFAULT 0,
                        explanatory_power FLOAT DEFAULT 0.5,
                        valence FLOAT DEFAULT 0.0,
                        arousal FLOAT DEFAULT 0.0,
                        promoted_to_hypothesis BOOLEAN DEFAULT FALSE
                    );
                """)
                logger.info("Table 'memories' created")
            else:
                logger.info("'memories' table present")

    except Exception as e:
        logger.error("Database initialization failed: %s", e)
        _pool = None


async def get_pool() -> Optional[asyncpg.Pool]:
    global _pool
    if _pool is None:
        await init_db()
    return _pool


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None