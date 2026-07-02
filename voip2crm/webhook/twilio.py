"""Twilio webhook adapter (the cheapest, most-controllable option).

Point Twilio's recordingStatusCallback at this receiver. The recording callback
carries RecordingUrl / RecordingSid / CallSid but not From/To, so we fetch the
Call resource via Twilio's REST API to fill those in. The recording download and
that REST call both use HTTP basic auth (AccountSid:AuthToken).

Signature: Twilio sends X-Twilio-Signature = base64(HMAC-SHA1(authToken,
fullUrl + concatenated sorted POST params)). Behind a tunnel/proxy, set
webhook.twilio.public_url so the signed URL matches what Twilio used.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .base import InboundCall, ProviderAdapter

log = logging.getLogger("voip2crm.webhook.twilio")


class TwilioAdapter(ProviderAdapter):
    def __init__(self, cfg: dict):
        self.account_sid = cfg.get("account_sid")
        self.auth_token = cfg.get("auth_token")
        self.public_url = cfg.get("public_url")  # optional, for signature behind proxy
        if not (self.account_sid and self.auth_token):
            raise ValueError("Twilio account_sid and auth_token are required.")

    def verify(self, request) -> bool:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            return False
        url = self.public_url or request.url
        params = request.form.to_dict()
        payload = url + "".join(k + params[k] for k in sorted(params))
        digest = hmac.new(self.auth_token.encode(), payload.encode(), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature)

    def parse(self, request) -> Optional[InboundCall]:
        form = request.form
        recording_url = form.get("RecordingUrl")
        call_sid = form.get("CallSid")
        if not recording_url or not call_sid:
            return None  # not a recording callback

        frm, to, direction, started = form.get("From"), form.get("To"), None, None
        if not (frm and to):
            frm, to, direction, started = self._fetch_call(call_sid, frm, to)

        return InboundCall(
            call_id=form.get("RecordingSid") or call_sid,
            direction=_norm_direction(direction or form.get("Direction")),
            from_number=frm or "",
            to_number=to or "",
            started_at=started,
            recording_url=recording_url,
            download_auth=(self.account_sid, self.auth_token),
        )

    def download(self, call: InboundCall, dest_dir: Path) -> Optional[str]:
        # Twilio recordings download as .mp3 (or .wav) by appending the extension.
        url = call.recording_url
        if not url.endswith((".mp3", ".wav")):
            url = url + ".mp3"
        r = requests.get(url, auth=call.download_auth, timeout=60)
        r.raise_for_status()
        out = Path(dest_dir) / f"{_safe(call.call_id)}.mp3"
        out.write_bytes(r.content)
        return str(out)

    def _fetch_call(self, call_sid: str, frm, to):
        """Look up From/To/direction/start from the Call resource."""
        try:
            url = (f"https://api.twilio.com/2010-04-01/Accounts/"
                   f"{self.account_sid}/Calls/{call_sid}.json")
            r = requests.get(url, auth=(self.account_sid, self.auth_token), timeout=30)
            r.raise_for_status()
            c = r.json()
            started = None
            if c.get("start_time"):
                try:
                    started = datetime.strptime(
                        c["start_time"], "%a, %d %b %Y %H:%M:%S %z"
                    ).astimezone(timezone.utc)
                except ValueError:
                    started = None
            return c.get("from") or frm, c.get("to") or to, c.get("direction"), started
        except Exception:
            log.exception("failed to fetch Twilio call %s", call_sid)
            return frm, to, None, None


def _norm_direction(d) -> str:
    d = (d or "").lower()
    return "incoming" if "in" in d else "outgoing"


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]", "_", s or "call")
