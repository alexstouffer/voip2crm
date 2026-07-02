"""OpenPhone / Quo webhook adapter.

Subscribe to the `call.recording.completed` event (create the webhook in the
OpenPhone app under Settings -> Integrations -> Webhooks, or via the API at
https://api.openphone.com/v1/webhooks/calls). The payload puts the recording
URL in data.object.media[].url. Example (trimmed):

  {"type": "call.recording.completed",
   "data": {"object": {"id": "AC...", "from": "+1...", "to": "+1...",
                        "direction": "incoming",
                        "media": [{"url": "https://storage.googleapis.com/..."}],
                        "answeredAt": "2022-01-24T19:28:42.000Z"}}}

Signature: OpenPhone signs with an `openphone-signature` header of the form
`hmac;1;<timestampMs>;<base64sig>`, where sig = HMAC-SHA256 over
`<timestampMs>.<rawBody>` keyed by the base64-decoded signing secret shown when
you create the webhook. Confirm the exact format against your webhook before
enabling verify_signatures — the shared ?token= check works regardless.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .base import InboundCall, ProviderAdapter

log = logging.getLogger("gv_crm.webhook.openphone")

_RECORDING_EVENT = "call.recording.completed"


class OpenPhoneAdapter(ProviderAdapter):
    def __init__(self, cfg: dict):
        self.signing_secret = cfg.get("signing_secret") or None

    def verify(self, request) -> bool:
        if not self.signing_secret:
            return True  # no secret configured; rely on the shared ?token= check
        header = request.headers.get("openphone-signature", "")
        try:
            scheme, version, ts, sig = header.split(";")
            key = base64.b64decode(self.signing_secret)
            msg = f"{ts}.".encode() + request.get_data()
            expected = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()
            return hmac.compare_digest(expected, sig)
        except Exception:
            log.warning("could not parse openphone-signature header")
            return False

    def parse(self, request) -> Optional[InboundCall]:
        payload = request.get_json(force=True, silent=True) or {}
        if payload.get("type") != _RECORDING_EVENT:
            return None
        obj = (payload.get("data") or {}).get("object") or {}
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


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]", "_", s or "call")
