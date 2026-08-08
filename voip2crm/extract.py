"""Interpret a call transcript.

Two layers:
  1. Rule-based (always on): keyword + date parsing. Cheap, no network. Also the
     fallback when the LLM is unreachable, so a down model never means a silently
     dropped follow-up.
  2. LLM (optional, local Ollama): one structured-JSON call that classifies the
     follow-up, decides conversation-vs-voicemail, and pulls contact details.

Design intent:
  - Follow-up is a SAFETY NET, so it errs toward catching real commitments, but
    it distinguishes a firm commitment from a soft brush-off from a flat no:
        committed | soft | not_interested | none
    Only `committed` creates a task; the rest are surfaced in the note for the
    rep to judge. Nothing is silently dropped.
  - Contact details (name/title/email) are FLAGGED for verification, never
    auto-written — phone-call ASR mangles spoken emails especially.

The LLM client is injectable so the whole pipeline is testable without Ollama:
pass llm_client=callable(system, user) -> dict.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Callable, Optional

from .models import CallRecord

try:
    import dateparser
except ImportError:
    dateparser = None

_HIGH_PRIORITY_HINTS = ("urgent", "asap", "emergency", "right away", "as soon as")
_DATE_PHRASE_RE = re.compile(
    r"\b("
    r"today|tomorrow|tonight|"
    r"(?:next|this)\s+\w+|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"by\s+\w+|"
    r"in\s+\d+\s+(?:day|days|hour|hours|week|weeks)"
    r")\b",
    re.IGNORECASE,
)
_VALID_STATUS = {"committed", "soft", "not_interested", "none"}


class Extractor:
    def __init__(self, cfg: dict, llm_client: Optional[Callable] = None):
        self.keywords = [k.lower() for k in cfg.get("followup_keywords", [])]
        self.use_llm = bool(cfg.get("use_llm", False))
        self.provider = cfg.get("llm_provider", "ollama")
        # Ollama (local)
        self.ollama_url = cfg.get("ollama_url", "http://localhost:11434/api/chat")
        self.ollama_model = cfg.get("ollama_model", "qwen3.5:4b")
        # Anthropic (cloud, still supported)
        self.anthropic_api_key = cfg.get("anthropic_api_key") or None
        self.anthropic_model = cfg.get("anthropic_model", "claude-sonnet-4-6")
        # Injectable client for tests / custom backends.
        self._client = llm_client

    # --- public ------------------------------------------------------------

    def enrich(self, rec: CallRecord) -> CallRecord:
        text = rec.best_transcript()
        if not text.strip():
            rec.summary = "(no transcript available)"
            rec.followup_status = "none"
            rec.is_conversation = False
            return rec

        # Rule-based baseline first; also the fallback if the LLM call fails.
        self._apply_rules(rec, text)

        if self.use_llm or self._client:
            try:
                data = self._call_llm(text)
                self._apply_llm_result(rec, data, text)
            except Exception as e:
                # Keep the rule-based result; note the degradation.
                rec.summary = rec.summary or ""
                rec.followup_reason = (rec.followup_reason + f" [llm unavailable: {e}]").strip()
        return rec

    # --- rule-based (baseline + fallback) ---------------------------------

    def _apply_rules(self, rec: CallRecord, text: str) -> None:
        low = text.lower()
        hits = [k for k in self.keywords if k in low]
        # Rules can only reliably tell "looks like a follow-up" vs "nothing".
        rec.followup_status = "committed" if hits else "none"
        rec.followup_needed = bool(hits)
        rec.followup_reason = (
            f"Keyword match: {', '.join(sorted(set(hits)))}" if hits else ""
        )
        if any(h in low for h in _HIGH_PRIORITY_HINTS):
            rec.priority = "HIGH"
        rec.followup_due = self._parse_due(text)
        rec.is_conversation = _looks_like_conversation(text)
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        rec.summary = " ".join(sentences[:2])[:400]

    @staticmethod
    def _parse_due(text: str) -> Optional[datetime]:
        if dateparser is None:
            return None
        m = _DATE_PHRASE_RE.search(text)
        if m:
            parsed = dateparser.parse(
                m.group(1),
                settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
            )
            if parsed:
                if parsed.hour == 0 and parsed.minute == 0:
                    parsed = parsed.replace(hour=9)
                return parsed
        return None

    # --- LLM ---------------------------------------------------------------

    def _system_prompt(self) -> str:
        return (
            "Analyze one outbound sales call transcript. Return ONLY JSON:\n"
            '{"is_conversation": bool, "summary": str, '
            '"followup_status": "committed"|"soft"|"not_interested"|"none", '
            '"followup_reason": str, "due_date": ISO8601|null, '
            '"priority": "LOW"|"MEDIUM"|"HIGH", '
            '"detected_contacts": [{"name": str|null, "title": str|null, "email": str|null}]}\n'
            "is_conversation: false if voicemail/answering machine/no answer.\n"
            "followup_status:\n"
            "- committed: both sides accept a concrete next step — a specific time "
            "(next Friday, next quarter) OR a requested deliverable (send a quote).\n"
            "- soft: vague/non-committal (let me think about it, reach out sometime). "
            "No due_date.\n"
            "- not_interested: explicit no / decline / do-not-contact.\n"
            "- none: no follow-up cue.\n"
            "detected_contacts: ONLY people/titles/emails explicitly stated in the "
            "transcript. If none are stated, return []. Never invent a contact. "
            "Spoken emails are often garbled — include your best guess but never "
            "fabricate one."
        )

    def _user_prompt(self, text: str) -> str:
        return f"Today is {datetime.now().date().isoformat()}.\n\nTranscript:\n{text}"

    def _call_llm(self, text: str) -> dict:
        if self._client is not None:
            return self._client(self._system_prompt(), self._user_prompt(text))
        if self.provider == "ollama":
            return self._ollama(text)
        if self.provider == "anthropic":
            return self._anthropic(text)
        raise ValueError(f"unknown llm_provider: {self.provider}")

    def _ollama(self, text: str) -> dict:
        import requests

        # format=json schema makes Ollama emit valid JSON at the token level.
        schema = {
            "type": "object",
            "properties": {
                "is_conversation": {"type": "boolean"},
                "summary": {"type": "string"},
                "followup_status": {"type": "string",
                                    "enum": ["committed", "soft", "not_interested", "none"]},
                "followup_reason": {"type": "string"},
                "due_date": {"type": ["string", "null"]},
                "priority": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                "detected_contacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": ["string", "null"]},
                            "title": {"type": ["string", "null"]},
                            "email": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["is_conversation", "followup_status"],
        }
        resp = requests.post(
            self.ollama_url,
            json={
                "model": self.ollama_model,
                "messages": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": self._user_prompt(text)},
                ],
                "stream": False,
                "think": False,          # critical on CPU: thinking chains take minutes
                "format": schema,
                "options": {"temperature": 0, "num_ctx": 16384},
            },
            timeout=600,
        )
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "{}")
        return json.loads(content)

    def _anthropic(self, text: str) -> dict:
        import requests

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.anthropic_model,
                "max_tokens": 900,
                "system": self._system_prompt(),
                "messages": [{"role": "user", "content": self._user_prompt(text)}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    def _apply_llm_result(self, rec: CallRecord, data: dict, text: str) -> None:
        status = (data.get("followup_status") or "none").lower()
        if status not in _VALID_STATUS:
            status = "none"
        rec.followup_status = status
        rec.followup_needed = status == "committed"
        rec.followup_reason = data.get("followup_reason") or rec.followup_reason
        rec.summary = data.get("summary") or rec.summary
        rec.priority = (data.get("priority") or rec.priority).upper()
        # Conversation vs voicemail — cross-check the model against a heuristic.
        llm_conv = data.get("is_conversation")
        heur_conv = _looks_like_conversation(text)
        if llm_conv is None:
            rec.is_conversation = heur_conv
        else:
            rec.is_conversation = bool(llm_conv)
            if bool(llm_conv) != heur_conv:
                rec.classification_uncertain = True

        # Contacts only from real conversations — a voicemail never yields a new
        # contact (company details come from the directory sheet instead).
        if rec.is_conversation:
            rec.detected_contacts = [c for c in (data.get("detected_contacts") or []) if _has_any(c)]
        else:
            rec.detected_contacts = []

        # Due date only for committed follow-ups.
        rec.followup_due = None
        if status == "committed":
            due = data.get("due_date")
            if due:
                try:
                    rec.followup_due = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                except ValueError:
                    rec.followup_due = self._parse_due(text)
            else:
                rec.followup_due = self._parse_due(text)


# --- helpers ---------------------------------------------------------------

def _has_any(c: dict) -> bool:
    return any((c.get(k) or "").strip() for k in ("name", "title", "email"))


def _looks_like_conversation(text: str) -> bool:
    """Heuristic: a real conversation has both speakers taking multiple turns.
    A voicemail is one-sided or very short."""
    agent = len(re.findall(r"(?im)^\s*agent\s*:", text))
    caller = len(re.findall(r"(?im)^\s*caller\s*:", text))
    if agent and caller:
        return agent >= 2 and caller >= 2
    # No speaker labels: fall back to length — a real call has substance.
    return len(text.split()) >= 40
