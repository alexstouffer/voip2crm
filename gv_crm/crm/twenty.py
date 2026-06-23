"""Twenty CRM adapter (self-hosted) using the Core REST API.

Base URL for self-hosted is https://<your-domain>/rest  (no /v1).
Auth: Authorization: Bearer <API_KEY>  (Settings -> API & Webhooks -> Create key).

Two Twenty-isms this adapter handles:
  1. Associations go through join objects: a note links to a person via
     /rest/noteTargets {noteId, personId}; a task via /rest/taskTargets.
  2. The note/task body field name is version-dependent. Older builds use a
     plain string `body`; recent builds use a rich-text `bodyV2` shaped like
     {"markdown": "..."}. Set crm.twenty.body_field accordingly (default "body").
     If a create 400s on the body field, the adapter retries with the other shape.

Twenty's REST docs are generated per-workspace; if a field name differs in your
instance, check Settings -> API & Webhooks -> Playground and adjust config.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from ..models import CallRecord
from .base import CRMAdapter


class TwentyAdapter(CRMAdapter):
    def __init__(self, cfg: dict):
        base = (cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise ValueError("Twenty base_url missing (e.g. https://crm.example.com/rest).")
        if not base.endswith("/rest"):
            base = base + "/rest"
        self.base = base
        token = cfg.get("api_key")
        if not token:
            raise ValueError("Twenty api_key missing (set TWENTY_API_KEY).")
        self.body_field = cfg.get("body_field", "body")          # "body" or "bodyV2"
        self.default_calling_code = cfg.get("default_calling_code")  # e.g. "+1", optional
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    # --- HTTP helpers ------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        r = self.session.post(f"{self.base}{path}", json=payload, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self.session.get(f"{self.base}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}

    @staticmethod
    def _extract_id(j: dict) -> str:
        """REST responses wrap the record like {"data": {"createNote": {"id": ...}}}.
        Be tolerant of variations."""
        data = j.get("data", j) if isinstance(j, dict) else {}
        if isinstance(data, dict):
            if "id" in data:
                return data["id"]
            for v in data.values():
                if isinstance(v, dict) and "id" in v:
                    return v["id"]
        raise RuntimeError(f"Could not find id in Twenty response: {j}")

    def _body_value(self, text: str) -> Any:
        return {"markdown": text} if self.body_field.endswith("V2") else text

    def _create_with_body(self, path: str, props: dict, text: str) -> str:
        """Create a note/task, retrying with the alternate body shape on 400."""
        attempts = [self.body_field, "bodyV2" if self.body_field == "body" else "body"]
        last_err: Optional[Exception] = None
        for field in attempts:
            value = {"markdown": text} if field.endswith("V2") else text
            payload = {**props, field: value}
            try:
                return self._extract_id(self._post(path, payload))
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 400:
                    last_err = e
                    continue
                raise
        raise RuntimeError(f"Twenty rejected note/task body on {path}: {last_err}")

    # --- CRMAdapter --------------------------------------------------------

    def upsert_contact(self, rec: CallRecord) -> str:
        phone = re.sub(r"[^\d+]", "", rec.caller_phone or "")
        if phone:
            found = self._find_person_by_phone(phone)
            if found:
                return found

        first, last = self._split_name(rec.caller_name or rec.display_name())
        props: dict = {"name": {"firstName": first, "lastName": last}}
        if phone:
            phones: dict = {"primaryPhoneNumber": phone}
            if self.default_calling_code:
                phones["primaryPhoneCallingCode"] = self.default_calling_code
            props["phones"] = phones
        return self._extract_id(self._post("/people", props))

    def add_note(self, contact_id: str, rec: CallRecord) -> str:
        title = f"Call — {rec.display_name()} — {self._date_str(rec.received_at)}"
        text = (
            f"**Summary:** {rec.summary}\n\n"
            f"**Caller:** {rec.display_name()}  \n"
            f"**Received:** {self._date_str(rec.received_at)}\n\n"
            f"---\n\n{rec.best_transcript()}"
        )
        note_id = self._create_with_body("/notes", {"title": title[:255]}, text)
        self._post("/noteTargets", {"noteId": note_id, "personId": contact_id})
        return note_id

    def create_followup_task(
        self, contact_id: str, title: str, due: Optional[datetime], body: str, priority: str
    ) -> str:
        props: dict = {"title": title[:255], "status": "TODO"}
        if due:
            props["dueAt"] = self._iso(due)
        # Twenty tasks have no native priority; fold it into the title for visibility.
        if priority and priority != "MEDIUM":
            props["title"] = f"[{priority}] {props['title']}"[:255]
        task_id = self._create_with_body("/tasks", props, body)
        self._post("/taskTargets", {"taskId": task_id, "personId": contact_id})
        return task_id

    # --- internals ---------------------------------------------------------

    def _find_person_by_phone(self, phone: str) -> Optional[str]:
        """Best-effort dedupe. Composite-field filtering varies by version, so
        failures here fall through to creating a new person."""
        try:
            j = self._get(
                "/people",
                params={"filter": f"phones.primaryPhoneNumber[eq]:{phone}", "limit": 1},
            )
            data = j.get("data", {})
            people = data.get("people") if isinstance(data, dict) else None
            if people:
                return people[0]["id"]
        except requests.HTTPError:
            pass
        return None

    @staticmethod
    def _split_name(full: str) -> tuple[str, str]:
        parts = (full or "").strip().split(" ", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return (parts[0] if parts else "Unknown"), ""

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _date_str(dt: Optional[datetime]) -> str:
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown"
