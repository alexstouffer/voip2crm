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
