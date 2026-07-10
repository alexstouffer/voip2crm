"""OpenPhone / Quo webhook adapter.

Handles two event types — subscribe to whichever fits your setup:

  call.recording.completed  -> downloads the recording; WhisperX transcribes it.
  call.transcript.completed -> uses Quo's AI transcript from the payload; no
                               recording download and no WhisperX needed.

Pick ONE per call to avoid double-processing (both map to the same call id, so
whichever event arrives first wins the dedupe). For the no-WhisperX setup,
subscribe to call.transcript.completed only (Quo Business plan or higher).

The transcript payload carries a `dialogue` array of speaker turns but not a
from/to; each turn has the speaker's number in `identifier`. We resolve the
external party by removing your own Quo number(s) — set webhook.openphone.
my_numbers — and treat the remaining number as the CRM contact.

Signature (confirmed against Quo docs): header `openphone-signature` =
`hmac;1;<timestampMs>;<base64sig>`, sig = HMAC-SHA256 over `<timestampMs>.<rawBody>`
keyed by the base64-decoded signing secret ("Reveal Signing Secret" in Quo).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .base import InboundCall, ProviderAdapter

log = logging.getLogger("voip2crm.webhook.openphone")


class OpenPhoneAdapter(ProviderAdapter):
    def __init__(self, cfg: dict):
        self.signing_secret = cfg.get("signing_secret") or None
        self.my_numbers = {_norm(n) for n in (cfg.get("my_numbers") or []) if n}

    def verify(self, request) -> bool:
        if not self.signing_secret:
            return True  # no secret configured; rely on the shared ?token= check
        header = request.headers.get("openphone-signature", "")
        try:
            _scheme, _version, ts, sig = header.split(";")
            key = base64.b64decode(self.signing_secret)
            msg = f"{ts}.".encode() + request.get_data()
            expected = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()
            return hmac.compare_digest(expected, sig)
        except Exception:
            log.warning("could not parse openphone-signature header")
            return False

    def parse(self, request) -> Optional[InboundCall]:
        payload = request.get_json(force=True, silent=True) or {}
        etype = payload.get("type") or ""
        obj = (payload.get("data") or {}).get("object") or {}

        if etype == "call.recording.completed":
            return self._parse_recording(payload, obj)
        if etype == "call.transcript.completed" or obj.get("object") == "callTranscript":
            return self._parse_transcript(obj)
        return None  # not an event we act on

    # --- recording event (WhisperX path) -----------------------------------

    def _parse_recording(self, payload: dict, obj: dict) -> Optional[InboundCall]:
        media = obj.get("media") or []
        url = next((m.get("url") for m in media if m.get("url")), None)
        if not url:
            log.warning("recording event with no media url: %s", obj.get("id"))
            return None
        return InboundCall(
            call_id=obj.get("id") or payload.get("id"),
            direction=(obj.get("direction") or "incoming"),
            from_number=obj.get("from") or "",
            to_number=obj.get("to") or "",
            started_at=_parse_ts(obj.get("answeredAt") or obj.get("createdAt")),
            recording_url=url,
        )

    # --- transcript event (no WhisperX) ------------------------------------

    def _parse_transcript(self, obj: dict) -> Optional[InboundCall]:
        dialogue = obj.get("dialogue") or []
        if not dialogue:
            log.warning("transcript event with empty dialogue: %s", obj.get("callId"))
            return None
        return InboundCall(
            call_id=obj.get("callId"),
            direction="unknown",
            party_number=self._counterparty(dialogue),
            started_at=_parse_ts(obj.get("createdAt")),
            transcript=self._render_dialogue(dialogue),
        )

    def _render_dialogue(self, dialogue: list) -> str:
        lines = []
        for turn in sorted(dialogue, key=lambda d: d.get("start", 0) or 0):
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            who = "Agent" if _norm(turn.get("identifier") or "") in self.my_numbers else "Caller"
            lines.append(f"{who}: {content}")
        return "\n".join(lines)

    def _counterparty(self, dialogue: list) -> Optional[str]:
        seen: list[str] = []  # original formatting, de-duped
        for turn in dialogue:
            ident = turn.get("identifier") or ""
            if ident and ident not in seen:
                seen.append(ident)
        external = [n for n in seen if _norm(n) not in self.my_numbers]
        if external:
            return external[0]
        if seen and not self.my_numbers:
            log.warning("set webhook.openphone.my_numbers to identify the caller reliably")
            return seen[0]
        return None

    # --- recording download (recording path only) --------------------------

    def download(self, call: InboundCall, dest_dir: Path) -> Optional[str]:
        r = requests.get(call.recording_url, timeout=60)
        r.raise_for_status()
        ext = _ext_from_content_type(r.headers.get("Content-Type", "")) or "mp3"
        out = Path(dest_dir) / f"{_safe(call.call_id)}.{ext}"
        out.write_bytes(r.content)
        return str(out)


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ext_from_content_type(ct: str) -> Optional[str]:
    ct = ct.lower()
    if "mpeg" in ct or "mp3" in ct:
        return "mp3"
    if "wav" in ct:
        return "wav"
    if "mp4" in ct or "m4a" in ct:
        return "m4a"
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^\d+]", "", s or "")


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]", "_", s or "call")
