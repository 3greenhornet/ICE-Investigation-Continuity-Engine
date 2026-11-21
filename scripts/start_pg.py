#!/usr/bin/env python3
"""Start an embedded PostgreSQL (pgserver) for ICE development/testing.

Prints the DATABASE_URL to stdout and keeps the server process referenced
in /tmp/ice_pg_uri so other shells can pick it up. Idempotent: reuses an
existing data dir.
"""
import sys
import pathlib

DATA_DIR = "/tmp/ice_pgdata"
URI_FILE = pathlib.Path("/tmp/ice_pg_uri")

def main():
    import pgserver
    db = pgserver.get_server(DATA_DIR)
    uri = db.get_uri()
    # asyncpg-compatible DSN with an explicit database name
    if "/postgres" in uri:
        dsn = uri
    else:
        dsn = uri
    URI_FILE.write_text(dsn)
    print(dsn)

if __name__ == "__main__":
    main()
