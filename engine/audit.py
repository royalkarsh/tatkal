"""Append only, SHA256 hash chained audit log, persisted in SQLite.

A hash chain makes later tampering evident. It does not prove that the original
input was true; it proves nobody quietly edited the record afterwards.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _hash(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY, ts TEXT, case_id TEXT, actor TEXT, kind TEXT,
            payload TEXT, prev TEXT, hash TEXT)""")
        self.db.commit()

    def append(self, kind: str, payload: dict, *, case_id: str = "", actor: str = "engine") -> dict:
        with self._lock:
            row = self.db.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (row[0] + 1) if row else 1
            prev = row[1] if row else "GENESIS"
            body = {"seq": seq, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "case_id": case_id, "actor": actor, "kind": kind, "payload": payload, "prev": prev}
            h = _hash(body)
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                            (seq, body["ts"], case_id, actor, kind, canonical(payload), prev, h))
            self.db.commit()
            return {**body, "hash": h}

    def events(self, case_id: str | None = None, limit: int | None = None) -> list[dict]:
        q = "SELECT seq, ts, case_id, actor, kind, payload, prev, hash FROM events"
        args: tuple = ()
        if case_id:
            q += " WHERE case_id = ?"
            args = (case_id,)
        q += " ORDER BY seq"
        rows = self.db.execute(q, args).fetchall()
        out = [{"seq": r[0], "ts": r[1], "case_id": r[2], "actor": r[3], "kind": r[4],
                "payload": json.loads(r[5]), "prev": r[6], "hash": r[7]} for r in rows]
        return out[-limit:] if limit else out

    def verify(self) -> dict:
        prev = "GENESIS"
        for e in self.events():
            body = {k: e[k] for k in ("seq", "ts", "case_id", "actor", "kind", "payload", "prev")}
            if e["prev"] != prev:
                return {"valid": False, "broken_at": e["seq"], "why": "previous hash does not match"}
            if _hash(body) != e["hash"]:
                return {"valid": False, "broken_at": e["seq"], "why": "content changed after it was written"}
            prev = e["hash"]
        return {"valid": True, "count": len(self.events()), "head": prev}

    def tamper_for_demo(self, seq: int, new_payload: dict) -> None:
        """Simulates an insider editing a past record, to show the chain catches it."""
        with self._lock:
            self.db.execute("UPDATE events SET payload = ? WHERE seq = ?", (canonical(new_payload), seq))
            self.db.commit()

    def simulate_tamper(self, seq: int, new_payload: dict) -> dict:
        """Copy the chain into memory, edit one past record there, and verify the copy.
        The real log is never touched, so the demo can be repeated."""
        shadow = AuditLog(":memory:")
        rows = self.db.execute("SELECT * FROM events ORDER BY seq").fetchall()
        shadow.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", rows)
        shadow.tamper_for_demo(seq, new_payload)
        return shadow.verify()

    def reset(self) -> None:
        with self._lock:
            self.db.execute("DELETE FROM events")
            self.db.commit()
