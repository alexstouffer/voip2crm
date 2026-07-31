"""The record that flows through the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class CallRecord:
    message_id: str                       # Gmail message id (idempotency key)
    received_at: Optional[datetime] = None
    subject: str = ""
    caller_name: Optional[str] = None
    caller_phone: Optional[str] = None
    # Business context from the local directory lookup (by phone).
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    company_domain: Optional[str] = None
    audio_path: Optional[str] = None
    recording_ref: Optional[str] = None   # where the archived recording lives (path or URL)
    google_transcript: str = ""           # Google's own transcript from the email body
    transcript: str = ""                  # WhisperX transcript (preferred)
    segments: list[dict] = field(default_factory=list)

    # Filled by extract.py
    summary: str = ""
    followup_needed: bool = False
    followup_reason: str = ""
    followup_due: Optional[datetime] = None
    priority: str = "MEDIUM"              # LOW | MEDIUM | HIGH

    # LLM extraction (Ollama)
    followup_status: str = "none"         # committed | soft | not_interested | none
    is_conversation: bool = True          # False = voicemail / no answer
    classification_uncertain: bool = False
    detected_contacts: list = field(default_factory=list)  # [{name,title,email}] — verify before saving
    conversation_number: int = 0          # Nth real conversation with this number
    voicemail_count: int = 0              # voicemails left to this number (incl. this one if VM)

    def best_transcript(self) -> str:
        return self.transcript or self.google_transcript or ""

    def display_name(self) -> str:
        return self.caller_name or self.caller_phone or "Unknown caller"
