"""Pull Google Voice voicemail/recording emails from Gmail and extract audio.

Why Gmail: Google Voice has no current official API for call audio. The
supported path is GV -> "Get voicemail via email" -> Gmail, where the message
carries Google's own transcript in the body and (depending on your GV settings)
the audio as an attachment. Enable forwarding in Google Voice:
  Settings -> Voicemail -> "Get voicemail via email".
For recorded calls, enable legacy incoming-call recording (press 4 during a call).
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Iterator, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .models import CallRecord

# modify = read mail + add/remove labels (our serverless dedupe), send = self-alerts.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_PHONE_RE = re.compile(r"(\+?\d[\d\-\.\s\(\)]{7,}\d)")


def _authenticate(credentials_path: str, token_path: str):
    creds: Optional[Credentials] = None
    tok = Path(token_path)
    if tok.exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            # Opens a browser the first time; cache the token afterwards.
            creds = flow.run_local_server(port=0)
        tok.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailSource:
    def __init__(self, cfg: dict, audio_dir: str):
        self.service = _authenticate(cfg["credentials_path"], cfg["token_path"])
        self.query = cfg.get("query", "from:(voice-noreply@google.com)")
        self.lookback_days = int(cfg.get("lookback_days", 7))
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def _full_query(self) -> str:
        after = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).strftime("%Y/%m/%d")
        return f"{self.query} after:{after}"

    def list_message_ids(self, limit: Optional[int] = None) -> list[str]:
        ids: list[str] = []
        page_token = None
        while True:
            resp = (
                self.service.users()
                .messages()
                .list(userId="me", q=self._full_query(), pageToken=page_token, maxResults=100)
                .execute()
            )
            for m in resp.get("messages", []):
                ids.append(m["id"])
                if limit and len(ids) >= limit:
                    return ids
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def fetch(self, message_id: str) -> CallRecord:
        msg = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        rec = CallRecord(message_id=message_id, subject=headers.get("subject", ""))

        date_hdr = headers.get("date")
        if date_hdr:
            try:
                rec.received_at = parsedate_to_datetime(date_hdr)
            except (TypeError, ValueError):
                rec.received_at = None

        body_text = self._collect_body(msg["payload"])
        rec.google_transcript = body_text.strip()
        rec.caller_phone = self._guess_phone(rec.subject, body_text)
        rec.caller_name = self._guess_name(rec.subject)

        audio = self._download_audio(message_id, msg["payload"])
        rec.audio_path = audio
        return rec

    # --- helpers -----------------------------------------------------------

    def _collect_body(self, payload: dict) -> str:
        """Walk MIME parts, prefer text/plain, fall back to stripped HTML."""
        plain, html = [], []

        def walk(part: dict) -> None:
            mime = part.get("mimeType", "")
            body = part.get("body", {})
            data = body.get("data")
            if data:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
                if mime == "text/plain":
                    plain.append(decoded)
                elif mime == "text/html":
                    html.append(decoded)
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        if plain:
            return "\n".join(plain)
        if html:
            stripped = re.sub(r"<[^>]+>", " ", "\n".join(html))
            return unescape(re.sub(r"[ \t]+", " ", stripped))
        return ""

    def _download_audio(self, message_id: str, payload: dict) -> Optional[str]:
        found: list[tuple[str, str]] = []  # (filename, attachment_id)

        def walk(part: dict) -> None:
            mime = part.get("mimeType", "")
            filename = part.get("filename", "") or ""
            att_id = part.get("body", {}).get("attachmentId")
            is_audio = mime.startswith("audio/") or filename.lower().endswith(
                (".mp3", ".wav", ".m4a", ".ogg", ".amr")
            )
            if att_id and is_audio:
                found.append((filename or f"{message_id}.mp3", att_id))
            for sub in part.get("parts", []) or []:
                walk(sub)

        walk(payload)
        if not found:
            return None

        filename, att_id = found[0]
        att = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=att_id)
            .execute()
        )
        raw = base64.urlsafe_b64decode(att["data"])
        safe = re.sub(r"[^\w\.\-]", "_", filename)
        out = self.audio_dir / f"{message_id}_{safe}"
        out.write_bytes(raw)
        return str(out)

    @staticmethod
    def _guess_phone(subject: str, body: str) -> Optional[str]:
        for src in (subject, body):
            m = _PHONE_RE.search(src or "")
            if m:
                return re.sub(r"[^\d+]", "", m.group(1))
        return None

    @staticmethod
    def _guess_name(subject: str) -> Optional[str]:
        # GV subjects look like: "New voicemail from John Smith"
        m = re.search(r"from\s+(.+?)\s*$", subject or "", re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # If it's just a number, treat as no name.
            if not re.fullmatch(r"[\d\-\.\s\(\)\+]+", name):
                return name
        return None

    def send_self_email(self, to_addr: str, subject: str, body: str) -> None:
        import email.message

        em = email.message.EmailMessage()
        em["To"] = to_addr
        em["Subject"] = subject
        em.set_content(body)
        raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
        self.service.users().messages().send(userId="me", body={"raw": raw}).execute()

    # --- label-based dedupe (stateless idempotency for serverless) ----------

    def ensure_label(self, name: str) -> str:
        """Return the id of the processed-marker label, creating it if needed."""
        existing = self.service.users().labels().list(userId="me").execute()
        for lbl in existing.get("labels", []):
            if lbl["name"] == name:
                return lbl["id"]
        created = (
            self.service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": name,
                    "labelListVisibility": "labelHide",
                    "messageListVisibility": "hide",
                },
            )
            .execute()
        )
        return created["id"]

    def add_label(self, message_id: str, label_id: str) -> None:
        self.service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [label_id]}
        ).execute()

    # --- push notifications (Gmail watch -> Pub/Sub) ------------------------

    def start_watch(self, topic_name: str, label_ids: Optional[list[str]] = None) -> dict:
        """(Re)register a Gmail push watch. Returns {historyId, expiration}.
        topic_name is the full Pub/Sub topic: projects/<proj>/topics/<topic>.
        Watches expire within 7 days — call this on a daily schedule to refresh."""
        body = {"topicName": topic_name, "labelFilterBehavior": "INCLUDE"}
        if label_ids:
            body["labelIds"] = label_ids
        return self.service.users().watch(userId="me", body=body).execute()
