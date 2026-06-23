"""Orchestrates: Gmail -> WhisperX -> extract -> CRM -> alerts."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .alerts import Alerts
from .config import Config
from .crm import build_adapter
from .extract import Extractor
from .gmail_source import GmailSource
from .models import CallRecord
from .state import State
from .transcribe import Transcriber

log = logging.getLogger("gv_crm")


class Pipeline:
    def __init__(self, cfg: Config, dry_run: bool = False, skip_transcribe: bool = False):
        cfg.ensure_dirs()
        self.cfg = cfg
        self.skip_transcribe = skip_transcribe
        self.transcript_dir = Path(cfg.get("storage", "transcript_dir") or "data/transcripts")

        self.gmail = GmailSource(cfg.section("gmail"), cfg.get("storage", "audio_dir"))
        self.transcriber = Transcriber(cfg.section("whisperx"))
        self.extractor = Extractor(cfg.section("extract"))

        crm_cfg = dict(cfg.section("crm"))
        if dry_run:
            crm_cfg["provider"] = "local"
        self.crm = build_adapter(crm_cfg)

        self.alerts = Alerts(cfg.section("alerts"), gmail_source=self.gmail)
        self.state = State(cfg.get("storage", "state_db") or "data/state.sqlite")

        # Dedupe strategy. With a processed_label set, idempotency lives in Gmail
        # itself (query excludes the label; we add it after processing) — so the
        # pipeline is stateless and safe on ephemeral compute like Lambda.
        self.processed_label_name = cfg.get("gmail", "processed_label")
        self.processed_label_id = None
        if self.processed_label_name:
            self.processed_label_id = self.gmail.ensure_label(self.processed_label_name)

    def run_once(self, limit: Optional[int] = None, reprocess: bool = False) -> int:
        ids = self.gmail.list_message_ids(limit=limit)
        log.info("Found %d candidate message(s).", len(ids))
        label_mode = self.processed_label_id is not None
        processed = 0
        for mid in ids:
            if not reprocess and not label_mode and self.state.seen(mid):
                log.debug("Skip already-processed %s", mid)
                continue
            try:
                self._process_one(mid)
                if label_mode:
                    self.gmail.add_label(mid, self.processed_label_id)
                else:
                    self.state.mark(mid)
                processed += 1
            except Exception:
                log.exception("Failed processing message %s", mid)
        log.info("Processed %d new message(s).", processed)
        return processed

    def _process_one(self, mid: str) -> None:
        rec = self.gmail.fetch(mid)
        log.info("Call from %s (%s)", rec.display_name(), rec.caller_phone or "no number")

        if not self.skip_transcribe and rec.audio_path:
            text, segments = self.transcriber.transcribe(rec.audio_path)
            rec.transcript, rec.segments = text, segments
        elif not rec.audio_path:
            log.info("  No audio attachment; using Google's email transcript as fallback.")

        self.extractor.enrich(rec)
        self._save_transcript(rec)

        contact_id = self.crm.upsert_contact(rec)
        self.crm.add_note(contact_id, rec)
        log.info("  Note added to contact %s", contact_id)

        if rec.followup_needed:
            title = f"Follow up: {rec.display_name()}"
            body = f"{rec.followup_reason}\n\nSummary: {rec.summary}"
            task_id = self.crm.create_followup_task(
                contact_id, title, rec.followup_due, body, rec.priority
            )
            self.alerts.fire(rec, task_id)
            due = rec.followup_due.isoformat() if rec.followup_due else "no date"
            log.info("  Follow-up task %s created (due %s, %s)", task_id, due, rec.priority)

    def _save_transcript(self, rec: CallRecord) -> None:
        out = self.transcript_dir / f"{rec.message_id}.json"
        out.write_text(
            json.dumps(
                {
                    "message_id": rec.message_id,
                    "caller_name": rec.caller_name,
                    "caller_phone": rec.caller_phone,
                    "received_at": rec.received_at.isoformat() if rec.received_at else None,
                    "summary": rec.summary,
                    "followup_needed": rec.followup_needed,
                    "followup_reason": rec.followup_reason,
                    "followup_due": rec.followup_due.isoformat() if rec.followup_due else None,
                    "priority": rec.priority,
                    "transcript": rec.best_transcript(),
                    "segments": rec.segments,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def close(self) -> None:
        self.state.close()
