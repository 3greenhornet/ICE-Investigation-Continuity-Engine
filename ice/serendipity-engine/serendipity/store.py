"""serendipity/store.py — persistence for the α/β loop.

Two interchangeable backends, same interface:
  * MemoryStore  — zero-config (tests, demos, throwaway sessions)
  * SQLiteStore  — one file per deployment, survives restarts

Nothing here knows about ICE; the adapter layer decides what a
"user_key" is (Hari session id) and what an item is (curiosity node).
Thread-safe: a coarse lock per store; single-writer is enough for a
chatbot's suggestion loop.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class _BaseStore:
    def arm(self, user: str, item: str) -> Tuple[int, int]: ...
    def bump_arm(self, user: str, item: str, alpha: int = 0, beta: int = 0) -> None: ...
    def global_counts(self, item: str) -> Tuple[int, int]: ...
    def bump_global(self, item: str, shown: int = 0, engaged: int = 0) -> None: ...
    def slot_counts(self, user: str, slot: str) -> Tuple[int, int]: ...
    def bump_slot(self, user: str, slot: str, shown: int = 0, engaged: int = 0) -> None: ...
    def put_token(self, token: str, user: str, item: str, slot: str, turn: int) -> None: ...
    def get_token(self, token: str) -> Optional[Tuple[str, str, str, int]]: ...
    def log_event(self, user: str, item: str, slot: str, signal: str) -> None: ...
    def events(self, user: Optional[str] = None) -> List[dict]: ...
    def export_json(self) -> str: ...
    def import_json(self, blob: str) -> None: ...
    def consume_token(self, token: str) -> bool: ...
    def close(self) -> None: ...


class MemoryStore(_BaseStore):
    def __init__(self) -> None:
        self._arms: Dict[Tuple[str, str], List[int]] = {}
        self._globals: Dict[str, List[int]] = {}
        self._slots: Dict[Tuple[str, str], List[int]] = {}
        self._tokens: Dict[str, Tuple[str, str, str, int]] = {}
        self._used_tokens = set()
        self._events: List[dict] = []
        self._lock = threading.Lock()

    def arm(self, user, item):
        with self._lock:
            a, b = self._arms.get((user, item), [0, 0])
            return a, b

    def bump_arm(self, user, item, alpha=0, beta=0):
        with self._lock:
            cur = self._arms.get((user, item), [0, 0])
            cur[0] += alpha
            cur[1] += beta
            self._arms[(user, item)] = cur

    def global_counts(self, item):
        with self._lock:
            s, e = self._globals.get(item, [0, 0])
            return s, e

    def bump_global(self, item, shown=0, engaged=0):
        with self._lock:
            cur = self._globals.get(item, [0, 0])
            cur[0] += shown
            cur[1] += engaged
            self._globals[item] = cur

    def slot_counts(self, user, slot):
        with self._lock:
            s, e = self._slots.get((user, slot), [0, 0])
            return s, e

    def bump_slot(self, user, slot, shown=0, engaged=0):
        with self._lock:
            cur = self._slots.get((user, slot), [0, 0])
            cur[0] += shown
            cur[1] += engaged
            self._slots[(user, slot)] = cur

    def put_token(self, token, user, item, slot, turn):
        with self._lock:
            self._tokens[token] = (user, item, slot, turn)

    def get_token(self, token):
        with self._lock:
            return self._tokens.get(token)

    def consume_token(self, token):
        with self._lock:
            if token not in self._tokens or token in self._used_tokens:
                return False
            self._used_tokens.add(token)
            return True

    def log_event(self, user, item, slot, signal):
        with self._lock:
            self._events.append(
                {"user": user, "item": item, "slot": slot,
                 "signal": signal, "ts": time.time()}
            )

    def events(self, user=None):
        with self._lock:
            return [e for e in self._events if user is None or e["user"] == user]

    def export_json(self):
        with self._lock:
            return json.dumps({
                "arms": {f"{u}|{i}": v for (u, i), v in self._arms.items()},
                "globals": self._globals,
                "slots": {f"{u}|{s}": v for (u, s), v in self._slots.items()},
                "tokens": self._tokens,
                "used_tokens": list(self._used_tokens),
                "events": self._events,
            })

    def import_json(self, blob):
        data = json.loads(blob)
        with self._lock:
            self._arms = {tuple(k.split("|", 1)): v for k, v in data.get("arms", {}).items()}
            self._globals = {k: v for k, v in data.get("globals", {}).items()}
            self._slots = {tuple(k.split("|", 1)): v for k, v in data.get("slots", {}).items()}
            self._tokens = data.get("tokens", {})
            self._used_tokens = set(data.get("used_tokens", []))
            self._events = data.get("events", [])

    def close(self):
        return None


class SQLiteStore(MemoryStore):
    """Same counters, durable. Falls back to MemoryStore semantics in RAM
    and flushes on every bump (chatbot write rates are trivial)."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS arms (
                user TEXT, item TEXT, alpha INT, beta INT,
                PRIMARY KEY (user, item));
            CREATE TABLE IF NOT EXISTS globals (
                item TEXT PRIMARY KEY, shown INT, engaged INT);
            CREATE TABLE IF NOT EXISTS slots (
                user TEXT, slot TEXT, shown INT, engaged INT,
                PRIMARY KEY (user, slot));
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY, user TEXT, item TEXT,
                slot TEXT, turn INT, consumed INT NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, item TEXT,
                slot TEXT, signal TEXT, ts REAL);
            """
        )
        try:
            self._db.execute(
                "ALTER TABLE tokens ADD COLUMN consumed INT NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        self._db.commit()
        self._load()

    def _load(self) -> None:
        for user, item, a, b in self._db.execute("SELECT user,item,alpha,beta FROM arms"):
            self._arms[(user, item)] = [a, b]
        for item, s, e in self._db.execute("SELECT item,shown,engaged FROM globals"):
            self._globals[item] = [s, e]
        for user, slot, s, e in self._db.execute("SELECT user,slot,shown,engaged FROM slots"):
            self._slots[(user, slot)] = [s, e]
        for token, user, item, slot, turn, consumed in self._db.execute(
            "SELECT token,user,item,slot,turn,consumed FROM tokens"
        ):
            self._tokens[token] = (user, item, slot, turn)
            if consumed:
                self._used_tokens.add(token)
        for _u, _i, _s, sig, ts in self._db.execute(
            "SELECT user,item,slot,signal,ts FROM events"
        ):
            self._events.append({"user": _u, "item": _i, "slot": _s, "signal": sig, "ts": ts})

    def _persist(self, sql, params):
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def bump_arm(self, user, item, alpha=0, beta=0):
        super().bump_arm(user, item, alpha, beta)
        a, b = self.arm(user, item)
        self._persist(
            "INSERT INTO arms (user,item,alpha,beta) VALUES (?,?,?,?) "
            "ON CONFLICT(user,item) DO UPDATE SET alpha=?, beta=?",
            (user, item, a, b, a, b),
        )

    def bump_global(self, item, shown=0, engaged=0):
        super().bump_global(item, shown, engaged)
        s, e = self.global_counts(item)
        self._persist(
            "INSERT INTO globals (item,shown,engaged) VALUES (?,?,?) "
            "ON CONFLICT(item) DO UPDATE SET shown=?, engaged=?",
            (item, s, e, s, e),
        )

    def bump_slot(self, user, slot, shown=0, engaged=0):
        super().bump_slot(user, slot, shown, engaged)
        s, e = self.slot_counts(user, slot)
        self._persist(
            "INSERT INTO slots (user,slot,shown,engaged) VALUES (?,?,?,?) "
            "ON CONFLICT(user,slot) DO UPDATE SET shown=?, engaged=?",
            (user, slot, s, e, s, e),
        )

    def put_token(self, token, user, item, slot, turn):
        super().put_token(token, user, item, slot, turn)
        self._persist(
            "INSERT OR REPLACE INTO tokens (token,user,item,slot,turn) VALUES (?,?,?,?,?)",
            (token, user, item, slot, turn),
        )

    def consume_token(self, token):
        with self._lock:
            if token not in self._tokens or token in self._used_tokens:
                return False
            cur = self._db.execute(
                "UPDATE tokens SET consumed=1 WHERE token=? AND consumed=0",
                (token,),
            )
            if cur.rowcount != 1:
                return False
            self._db.commit()
            self._used_tokens.add(token)
            return True

    def log_event(self, user, item, slot, signal):
        super().log_event(user, item, slot, signal)
        self._persist(
            "INSERT INTO events (user,item,slot,signal,ts) VALUES (?,?,?,?,?)",
            (user, item, slot, signal, time.time()),
        )

    def import_json(self, blob):
        super().import_json(blob)
        with self._lock:
            self._db.execute("DELETE FROM arms")
            self._db.execute("DELETE FROM globals")
            self._db.execute("DELETE FROM slots")
            self._db.execute("DELETE FROM tokens")
            self._db.execute("DELETE FROM events")
            self._db.executemany(
                "INSERT INTO arms(user,item,alpha,beta) VALUES(?,?,?,?)",
                [(u, i, v[0], v[1]) for (u, i), v in self._arms.items()],
            )
            self._db.executemany(
                "INSERT INTO globals(item,shown,engaged) VALUES(?,?,?)",
                [(i, v[0], v[1]) for i, v in self._globals.items()],
            )
            self._db.executemany(
                "INSERT INTO slots(user,slot,shown,engaged) VALUES(?,?,?,?)",
                [(u, s, v[0], v[1]) for (u, s), v in self._slots.items()],
            )
            self._db.executemany(
                "INSERT INTO tokens(token,user,item,slot,turn,consumed) VALUES(?,?,?,?,?,?)",
                [
                    (t, u, i, s, turn, int(t in self._used_tokens))
                    for t, (u, i, s, turn) in self._tokens.items()
                ],
            )
            self._db.executemany(
                "INSERT INTO events(user,item,slot,signal,ts) VALUES(?,?,?,?,?)",
                [(e["user"], e["item"], e["slot"], e["signal"], e["ts"]) for e in self._events],
            )
            self._db.commit()

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None


def make_store(path: Optional[str]) -> _BaseStore:
    if path is None:
        return MemoryStore()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SQLiteStore(path)
