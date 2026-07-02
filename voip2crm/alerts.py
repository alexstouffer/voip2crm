"""Extra follow-up reminders beyond the CRM task: a CSV log and an optional
self-email via Gmail."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import CallRecord


class Alerts:
    def __init__(self, cfg: dict, gmail_source=None):
        self.csv_path = cfg.get("followups_csv")
        self.email_self = bool(cfg.get("email_self", False))
        self.email_to = cfg.get("email_to")
        self.gmail = gmail_source

    def fire(self, rec: CallRecord, task_id: str) -> None:
        if self.csv_path:
            self._append_csv(rec, task_id)
        if self.email_self and self.gmail and self.email_to:
            self._email(rec, task_id)

    def _append_csv(self, rec: CallRecord, task_id: str) -> None:
        path = Path(self.csv_path)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["logged_at", "caller", "phone", "due", "priority", "reason", "task_id"])
            w.writerow([
                datetime.now().isoformat(),
                rec.display_name(),
                rec.caller_phone or "",
                rec.followup_due.isoformat() if rec.followup_due else "",
                rec.priority,
                rec.followup_reason,
                task_id,
            ])

    def _email(self, rec: CallRecord, task_id: str) -> None:
        due = rec.followup_due.strftime("%a %b %d, %I:%M %p") if rec.followup_due else "no date set"
        subject = f"[Follow-up] {rec.display_name()} — {rec.priority}"
        body = (
            f"A new call needs follow-up.\n\n"
            f"Caller:   {rec.display_name()}\n"
            f"Phone:    {rec.caller_phone or 'unknown'}\n"
            f"Due:      {due}\n"
            f"Priority: {rec.priority}\n"
            f"Reason:   {rec.followup_reason}\n\n"
            f"Summary:  {rec.summary}\n\n"
            f"(CRM task id: {task_id})"
        )
        self.gmail.send_self_email(self.email_to, subject, body)
