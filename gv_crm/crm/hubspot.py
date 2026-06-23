"""HubSpot reference adapter using a Private App token (CRM API v3).

Create a Private App in HubSpot (Settings -> Integrations -> Private Apps) with
scopes: crm.objects.contacts.read/write, and the tasks scope. Put the token in
HUBSPOT_TOKEN. Association type ids below are HubSpot-defined defaults:
  note  -> contact : 202
  task  -> contact : 204
Verify against current HubSpot docs if an association call ever errors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

from ..models import CallRecord
from .base import CRMAdapter

BASE = "https://api.hubapi.com"
NOTE_TO_CONTACT = 202
TASK_TO_CONTACT = 204


class HubSpotAdapter(CRMAdapter):
    def __init__(self, cfg: dict):
        self.token = cfg.get("access_token")
        if not self.token:
            raise ValueError("HubSpot access_token missing (set HUBSPOT_TOKEN).")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        )

    def _post(self, path: str, payload: dict) -> dict:
        r = self.session.post(f"{BASE}{path}", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def upsert_contact(self, rec: CallRecord) -> str:
        phone = rec.caller_phone
        if phone:
            search = self._post(
                "/crm/v3/objects/contacts/search",
                {
                    "filterGroups": [
                        {"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone}]}
                    ],
                    "properties": ["phone"],
                    "limit": 1,
                },
            )
            results = search.get("results", [])
            if results:
                return results[0]["id"]

        props = {}
        if rec.caller_name:
            parts = rec.caller_name.split(" ", 1)
            props["firstname"] = parts[0]
            if len(parts) > 1:
                props["lastname"] = parts[1]
        if phone:
            props["phone"] = phone
        created = self._post("/crm/v3/objects/contacts", {"properties": props})
        return created["id"]

    def add_note(self, contact_id: str, rec: CallRecord) -> str:
        body = (
            f"Call summary: {rec.summary}\n\n"
            f"Caller: {rec.display_name()}\n"
            f"Received: {rec.received_at.isoformat() if rec.received_at else 'unknown'}\n\n"
            f"--- Transcript ---\n{rec.best_transcript()}"
        )
        payload = {
            "properties": {
                "hs_note_body": body[:65000],
                "hs_timestamp": _now_ms(),
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED",
                         "associationTypeId": NOTE_TO_CONTACT}
                    ],
                }
            ],
        }
        return self._post("/crm/v3/objects/notes", payload)["id"]

    def create_followup_task(
        self, contact_id: str, title: str, due: Optional[datetime], body: str, priority: str
    ) -> str:
        due_ms = _to_ms(due) if due else _now_ms()
        payload = {
            "properties": {
                "hs_task_subject": title[:255],
                "hs_task_body": body[:65000],
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": priority if priority in ("LOW", "MEDIUM", "HIGH") else "MEDIUM",
                "hs_timestamp": due_ms,
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED",
                         "associationTypeId": TASK_TO_CONTACT}
                    ],
                }
            ],
        }
        return self._post("/crm/v3/objects/tasks", payload)["id"]


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
