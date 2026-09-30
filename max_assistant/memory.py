"""Long-term memory: facts you ask Max to remember, a searchable log of past conversations,
and a log of actions (for the dashboard). One SQLite file (data/max.db) plus local
embeddings for "search by meaning".

Embeddings come from bge-small-en (fastembed, ONNX on the CPU, ~28 ms per text), so the GPU
stays free for the LLM. Vectors are kept in memory and compared with a dot product, which
is instant at personal scale (thousands of items); no vector database needed.

Measured similarity scores: a query and the fact it is about score 0.74-0.88; unrelated
pairs score at most ~0.61. RECALL_MIN sits between the two.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .config import resolve_path

log = logging.getLogger(__name__)

RECALL_MIN = 0.68        # attach a memory to a request automatically above this score
DUPLICATE_MIN = 0.93     # "remember X" when a near-identical fact exists updates it instead

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL, vec BLOB);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, user TEXT NOT NULL, reply TEXT NOT NULL,
    tools TEXT NOT NULL DEFAULT '[]', vec BLOB);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, started TEXT NOT NULL, ended TEXT NOT NULL,
    folder TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', words INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, due TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', fired TEXT);
"""


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...     # (n, dim), rows L2-normalised


class FastEmbedder:
    """bge-small-en via fastembed. Loads lazily (first use takes a few seconds)."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        self.model_name = model
        self._model = None
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self.model_name)
            vecs = np.array(list(self._model.embed(texts)), dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


@dataclass
class Hit:
    id: int
    text: str
    score: float
    ts: str = ""


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, db_path: str = "data/max.db", embedder: Embedder | None = None):
        path = resolve_path(db_path) if db_path != ":memory:" else db_path
        if path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the voice loop and the dashboard server, guarded by a lock
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.embedder = embedder or FastEmbedder()
        self._facts = self._load_vectors("facts", "text")
        self._turns = self._load_vectors("turns", "user || ' → ' || reply")

    # ----- vectors -----
    def _load_vectors(self, table: str, text_expr: str) -> dict:
        rows = self.db.execute(f"SELECT id, {text_expr} AS t, vec FROM {table}").fetchall()
        ids, vecs, missing = [], [], []
        for r in rows:
            if r["vec"] is None:
                missing.append((r["id"], r["t"]))
            else:
                ids.append(r["id"])
                vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
        index = {"ids": ids, "vecs": vecs}
        for rid, text in missing:             # e.g. rows added while embeddings were unavailable
            self._index(index, table, rid, text)
        return index

    def _index(self, index: dict, table: str, rid: int, text: str):
        vec = self.embedder.embed([text])[0]
        self.db.execute(f"UPDATE {table} SET vec = ? WHERE id = ?", (vec.tobytes(), rid))
        self.db.commit()
        index["ids"].append(rid)
        index["vecs"].append(vec)

    def _search(self, index: dict, query: str, k: int) -> list[tuple[int, float]]:
        if not index["ids"]:
            return []
        q = self.embedder.embed([query])[0]
        scores = np.stack(index["vecs"]) @ q
        best = np.argsort(-scores)[:k]
        return [(index["ids"][i], float(scores[i])) for i in best]

    def _unindex(self, index: dict, rid: int):
        if rid in index["ids"]:
            i = index["ids"].index(rid)
            del index["ids"][i], index["vecs"][i]

    # ----- facts -----
    def add_fact(self, text: str) -> tuple[int, str | None]:
        """Save a fact. Returns (id, old_text) where old_text is set if a near-duplicate
        fact was updated instead of adding a new one."""
        text = text.strip().rstrip(".")
        with self.lock:
            for rid, score in self._search(self._facts, text, 1):
                if score >= DUPLICATE_MIN:
                    old = self.db.execute("SELECT text FROM facts WHERE id = ?", (rid,)).fetchone()["text"]
                    self._unindex(self._facts, rid)
                    self.db.execute("UPDATE facts SET text = ?, created = ? WHERE id = ?", (text, now_iso(), rid))
                    self._index(self._facts, "facts", rid, text)
                    return rid, old
            cur = self.db.execute("INSERT INTO facts (text, created) VALUES (?, ?)", (text, now_iso()))
            self._index(self._facts, "facts", cur.lastrowid, text)
            return cur.lastrowid, None

    def search_facts(self, query: str, k: int = 3, min_score: float = 0.0) -> list[Hit]:
        with self.lock:
            hits = [(rid, s) for rid, s in self._search(self._facts, query, k) if s >= min_score]
            out = []
            for rid, score in hits:
                r = self.db.execute("SELECT text, created FROM facts WHERE id = ?", (rid,)).fetchone()
                out.append(Hit(rid, r["text"], score, r["created"]))
            return out

    def facts(self) -> list[Hit]:
        with self.lock:
            rows = self.db.execute("SELECT id, text, created FROM facts ORDER BY id DESC").fetchall()
            return [Hit(r["id"], r["text"], 1.0, r["created"]) for r in rows]

    def delete_fact(self, fact_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self.db.commit()
            self._unindex(self._facts, fact_id)
            return cur.rowcount > 0

    # ----- conversation log -----
    def log_turn(self, user: str, reply: str, tools: list[str] | None = None) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO turns (ts, user, reply, tools) VALUES (?, ?, ?, ?)",
                                  (now_iso(), user, reply, json.dumps(tools or [])))
            self._index(self._turns, "turns", cur.lastrowid, f"{user} → {reply}")
            return cur.lastrowid

    def search_turns(self, query: str, k: int = 4, min_score: float = 0.0) -> list[Hit]:
        with self.lock:
            out = []
            for rid, score in self._search(self._turns, query, k):
                if score < min_score:
                    continue
                r = self.db.execute("SELECT ts, user, reply FROM turns WHERE id = ?", (rid,)).fetchone()
                out.append(Hit(rid, f"you: {r['user']} / Max: {r['reply']}", score, r["ts"]))
            return out

    def recent_turns(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, ts, user, reply, tools FROM turns ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
            return [dict(r, tools=json.loads(r["tools"])) for r in reversed(rows)]

    def turns_between(self, start: str, end: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, ts, user, reply FROM turns WHERE ts >= ? AND ts < ? ORDER BY id",
                                   (start, end)).fetchall()
            return [dict(r) for r in rows]

    # ----- action log (dashboard) -----
    def log_action(self, kind: str, detail: dict) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO actions (ts, kind, detail) VALUES (?, ?, ?)",
                                  (now_iso(), kind, json.dumps(detail, default=str)))
            self.db.commit()
            return cur.lastrowid

    def actions(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, ts, kind, detail FROM actions ORDER BY id DESC LIMIT ?",
                                   (limit,)).fetchall()
            return [dict(r, detail=json.loads(r["detail"])) for r in reversed(rows)]

    # ----- meeting / lecture notes -----
    def add_notes(self, title: str, kind: str, started: str, ended: str, folder: str, summary: str, words: int) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO notes (title, kind, started, ended, folder, summary, words) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?)", (title, kind, started, ended, folder, summary, words))
            self.db.commit()
            return cur.lastrowid

    def notes(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def note(self, note_id: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            return dict(row) if row else None

    # ----- small key/value state (e.g. when the daily digest was last sent) -----
    def get(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set(self, key: str, value: str):
        with self.lock:
            self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (key, value))
            self.db.commit()

    # ----- automatic recall for the agent -----
    def context_for(self, user_text: str) -> str | None:
        """Facts relevant enough to attach to a request, or None."""
        try:
            hits = self.search_facts(user_text, k=3, min_score=RECALL_MIN)
        except Exception as exc:                       # never let memory break a request
            log.warning("memory recall failed: %s", exc)
            return None
        return "; ".join(h.text for h in hits) or None
