"""Telephony webhook provider interface.

A provider adapter turns an incoming HTTP webhook (from OpenPhone/Quo, Twilio,
etc.) into a normalized InboundCall, verifies its authenticity, and downloads
the recording audio. The rest of the pipeline is provider-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class InboundCall:
    call_id: str                        # provider call id (dedupe key)
    direction: str                      # "incoming" | "outgoing" | "unknown"
    from_number: str = ""
    to_number: str = ""
    started_at: Optional[datetime] = None
    recording_url: Optional[str] = None
    # Optional auth for fetching the recording (Twilio needs basic auth).
    download_auth: Optional[tuple] = None
    download_headers: dict = field(default_factory=dict)
    # Provider-supplied transcript (Quo AI). When set, no audio download or
    # WhisperX is needed — the pipeline uses this text directly.
    transcript: Optional[str] = None
    # Explicit external-party number (used when direction is unknown, e.g. from
    # a transcript event where we resolve the counterparty ourselves).
    party_number: Optional[str] = None

    def counterparty(self) -> str:
        """The external party's number — the caller on inbound, callee on outbound."""
        if self.party_number:
            return self.party_number
        return self.from_number if self.direction.startswith("in") else self.to_number


class ProviderAdapter(ABC):
    @abstractmethod
    def verify(self, request) -> bool:
        """Return True if the request is authentic (signature check)."""

    @abstractmethod
    def parse(self, request) -> Optional[InboundCall]:
        """Return an InboundCall for recording-ready events, else None."""

    @abstractmethod
    def download(self, call: InboundCall, dest_dir: Path) -> Optional[str]:
        """Download the recording into dest_dir; return the file path."""


def build_provider(webhook_cfg: dict) -> ProviderAdapter:
    provider = (webhook_cfg.get("provider") or "openphone").lower()
    if provider == "openphone":
        from .openphone import OpenPhoneAdapter
        return OpenPhoneAdapter(webhook_cfg.get("openphone", {}))
    if provider == "twilio":
        from .twilio import TwilioAdapter
        return TwilioAdapter(webhook_cfg.get("twilio", {}))
    raise ValueError(f"Unknown webhook provider: {provider!r}")
