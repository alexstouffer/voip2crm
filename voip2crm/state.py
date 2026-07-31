"""Tracks which items we've already processed, so reruns/duplicates are safe.

The webhook receiver creates this on the main thread but reads/writes it from a
background worker thread, so the connection is opened with check_same_thread=
False and every access is guarded by a lock.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class State:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS processed (
                       message_id TEXT PRIMARY KEY,
                       processed_at TEXT DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS call_log (
                       call_id TEXT PRIMARY KEY,
                       phone TEXT,
                       is_conversation INTEGER,
                       logged_at TEXT DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_phone ON call_log(phone)"
            )
            self.conn.commit()

    def seen(self, message_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
            )
            return cur.fetchone() is not None

    def mark(self, message_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO processed (message_id) VALUES (?)", (message_id,)
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # --- call log (conversations vs voicemails, by phone) ------------------

    @staticmethod
    def _norm_phone(phone: str) -> str:
        import re
        d = re.sub(r"\D", "", phone or "")
        return d[-10:] if len(d) >= 10 else d

    def call_counts(self, phone: str) -> tuple[int, int]:
        """Return (prior_conversations, prior_voicemails) for a number."""
        key = self._norm_phone(phone)
        if not key:
            return (0, 0)
        with self._lock:
            cur = self.conn.execute(
                "SELECT COALESCE(SUM(is_conversation),0), "
                "COALESCE(SUM(1-is_conversation),0) FROM call_log WHERE phone = ?",
                (key,),
            )
            conv, vm = cur.fetchone()
            return (int(conv or 0), int(vm or 0))

    def log_call(self, phone: str, call_id: str, is_conversation: bool) -> None:
        key = self._norm_phone(phone)
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO call_log (call_id, phone, is_conversation) "
                "VALUES (?, ?, ?)",
                (call_id, key, 1 if is_conversation else 0),
            )
            self.conn.commit()
