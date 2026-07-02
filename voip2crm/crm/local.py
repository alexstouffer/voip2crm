"""A no-external-dependency CRM that writes to SQLite + JSON. Use this to run
the entire pipeline end-to-end before you wire up a real CRM."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import CallRecord
from .base import CRMAdapter


class LocalAdapter(CRMAdapter):
    def __init__(self, cfg: dict):
        db = cfg.get("local_db", "data/crm_local.sqlite")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY, name TEXT, phone TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, contact_id TEXT, body TEXT, created TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, contact_id TEXT, title TEXT,
                due TEXT, body TEXT, priority TEXT, created TEXT
            );
            """
        )
        self.conn.commit()

    def upsert_contact(self, rec: CallRecord) -> str:
        phone = rec.caller_phone or ""
        if phone:
            row = self.conn.execute(
                "SELECT id FROM contacts WHERE phone = ?", (phone,)
            ).fetchone()
            if row:
                return row[0]
        cid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO contacts (id, name, phone) VALUES (?, ?, ?)",
            (cid, rec.caller_name or rec.display_name(), phone or None),
        )
        self.conn.commit()
        return cid

    def add_note(self, contact_id: str, rec: CallRecord) -> str:
        nid = str(uuid.uuid4())
        body = json.dumps(
            {
                "summary": rec.summary,
                "transcript": rec.best_transcript(),
                "received_at": rec.received_at.isoformat() if rec.received_at else None,
            },
            ensure_ascii=False,
            indent=2,
        )
        self.conn.execute(
            "INSERT INTO notes (id, contact_id, body, created) VALUES (?, ?, ?, ?)",
            (nid, contact_id, body, datetime.now().isoformat()),
        )
        self.conn.commit()
        return nid

    def create_followup_task(
        self, contact_id: str, title: str, due: Optional[datetime], body: str, priority: str
    ) -> str:
        tid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO tasks (id, contact_id, title, due, body, priority, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, contact_id, title, due.isoformat() if due else None, body, priority,
             datetime.now().isoformat()),
        )
        self.conn.commit()
        return tid
