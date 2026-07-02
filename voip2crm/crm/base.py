"""CRM adapter interface. Implement these three methods for any CRM."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models import CallRecord


class CRMAdapter(ABC):
    @abstractmethod
    def upsert_contact(self, rec: CallRecord) -> str:
        """Find-or-create the contact. Return a CRM contact id."""

    @abstractmethod
    def add_note(self, contact_id: str, rec: CallRecord) -> str:
        """Attach the call transcript/summary as a note. Return note id."""

    @abstractmethod
    def create_followup_task(
        self, contact_id: str, title: str, due: Optional[datetime], body: str, priority: str
    ) -> str:
        """Create a follow-up task/alert. Return task id."""


def build_adapter(crm_cfg: dict) -> CRMAdapter:
    provider = (crm_cfg.get("provider") or "local").lower()
    if provider == "local":
        from .local import LocalAdapter
        return LocalAdapter(crm_cfg)
    if provider == "hubspot":
        from .hubspot import HubSpotAdapter
        return HubSpotAdapter(crm_cfg.get("hubspot", {}))
    if provider == "twenty":
        from .twenty import TwentyAdapter
        return TwentyAdapter(crm_cfg.get("twenty", {}))
    raise ValueError(f"Unknown CRM provider: {provider!r}")
