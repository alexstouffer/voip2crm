"""Decide whether a call needs follow-up and when.

Two layers:
  1. Rule-based (always on): keyword match + date parsing. Cheap, no network.
  2. LLM (optional): a single structured-JSON call for a cleaner summary and
     more reliable follow-up reasoning. Enable with extract.use_llm: true.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Optional

from .models import CallRecord

try:
    import dateparser
except ImportError:  # graceful degradation
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


class Extractor:
    def __init__(self, cfg: dict):
        self.keywords = [k.lower() for k in cfg.get("followup_keywords", [])]
        self.use_llm = bool(cfg.get("use_llm", False))
        self.provider = cfg.get("llm_provider", "anthropic")
        self.anthropic_api_key = cfg.get("anthropic_api_key") or None
        self.anthropic_model = cfg.get("anthropic_model", "claude-sonnet-4-6")

    def enrich(self, rec: CallRecord) -> CallRecord:
        text = rec.best_transcript()
        if not text.strip():
            rec.summary = "(no transcript available)"
            return rec

        # Rule-based first (also serves as fallback if the LLM call fails).
        self._apply_rules(rec, text)

        if self.use_llm and self.anthropic_api_key:
            try:
                self._apply_llm(rec, text)
            except Exception as e:  # keep rule-based result on failure
                rec.summary = rec.summary or f"(LLM extraction failed: {e})"
        return rec

    # --- rule-based --------------------------------------------------------

    def _apply_rules(self, rec: CallRecord, text: str) -> None:
        low = text.lower()
        hits = [k for k in self.keywords if k in low]
        rec.followup_needed = bool(hits)
        rec.followup_reason = (
            f"Caller language suggests follow-up: {', '.join(sorted(set(hits)))}"
            if hits else ""
        )
        if any(h in low for h in _HIGH_PRIORITY_HINTS):
            rec.priority = "HIGH"

        rec.followup_due = self._parse_due(text)
        # Short, deterministic summary: first ~2 sentences.
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        rec.summary = " ".join(sentences[:2])[:400]

    @staticmethod
    def _parse_due(text: str) -> Optional[datetime]:
        if dateparser is None:
            return None
        m = _DATE_PHRASE_RE.search(text)
        base = datetime.now()
        if m:
            parsed = dateparser.parse(
                m.group(1),
                settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": base},
            )
            if parsed:
                # Default reminders to 9am if no time component was given.
                if parsed.hour == 0 and parsed.minute == 0:
                    parsed = parsed.replace(hour=9)
                return parsed
        # No explicit date but follow-up flagged elsewhere -> default next business morning.
        return None

    # --- LLM ---------------------------------------------------------------

    def _apply_llm(self, rec: CallRecord, text: str) -> None:
        import requests

        system = (
            "You analyze a phone call transcript and return ONLY a JSON object, "
            "no markdown, no preamble. Schema: "
            '{"summary": str, "contact_name": str|null, "contact_phone": str|null, '
            '"followup_needed": bool, "followup_reason": str, '
            '"due_date": str|null (ISO 8601), "priority": "LOW"|"MEDIUM"|"HIGH"}'
        )
        user = f"Today is {datetime.now().date().isoformat()}.\n\nTranscript:\n{text}"

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.anthropic_model,
                "max_tokens": 700,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        rec.summary = data.get("summary") or rec.summary
        rec.caller_name = data.get("contact_name") or rec.caller_name
        rec.caller_phone = data.get("contact_phone") or rec.caller_phone
        rec.followup_needed = bool(data.get("followup_needed", rec.followup_needed))
        rec.followup_reason = data.get("followup_reason") or rec.followup_reason
        rec.priority = (data.get("priority") or rec.priority).upper()
        due = data.get("due_date")
        if due:
            try:
                rec.followup_due = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError:
                pass
