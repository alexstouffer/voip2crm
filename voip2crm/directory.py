"""Local contact directory: look up an inbound phone number in a CSV you
maintain (exported from another service) and return the business details for it.

Matching is by the last 10 digits, so formatting and country-code differences
don't matter — "12148926980" in the sheet matches "+12148926980" from the call.
The file is reloaded automatically when it changes on disk, so you can refresh
the export without restarting the service.
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("voip2crm.directory")


class Directory:
    def __init__(self, cfg: dict):
        self.path = cfg.get("path")
        self.phone_col = cfg.get("phone_column", "phone")
        self.company_col = cfg.get("company_column", "company")
        self.website_col = cfg.get("website_column", "website")
        self.domain_col = cfg.get("domain_column", "domain")
        self._index: dict[str, dict] = {}
        self._mtime: Optional[float] = None
        self._load()

    def _load(self) -> None:
        if not self.path or not Path(self.path).exists():
            if self.path:
                log.warning("directory file not found: %s (enrichment disabled)", self.path)
            self._index = {}
            self._mtime = None
            return
        index: dict[str, dict] = {}
        # utf-8-sig strips a BOM if the export has one.
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = _norm(row.get(self.phone_col, ""))
                if not key:
                    continue
                index.setdefault(key, {
                    "company": (row.get(self.company_col) or "").strip(),
                    "website": (row.get(self.website_col) or "").strip(),
                    "domain": (row.get(self.domain_col) or "").strip(),
                })
        self._index = index
        self._mtime = Path(self.path).stat().st_mtime
        log.info("loaded directory: %d numbers from %s", len(index), self.path)

    def _maybe_reload(self) -> None:
        if not self.path or not Path(self.path).exists():
            return
        mtime = Path(self.path).stat().st_mtime
        if mtime != self._mtime:
            log.info("directory file changed; reloading")
            self._load()

    def lookup(self, phone: str) -> Optional[dict]:
        """Return {company, website, domain} for a number, or None if unlisted."""
        self._maybe_reload()
        return self._index.get(_norm(phone))


def _norm(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d
