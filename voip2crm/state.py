"""Tracks which Gmail messages we've already processed, so reruns are safe."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class State:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS processed (
                   message_id TEXT PRIMARY KEY,
                   processed_at TEXT DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        self.conn.commit()

    def seen(self, message_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
        )
        return cur.fetchone() is not None

    def mark(self, message_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed (message_id) VALUES (?)", (message_id,)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
