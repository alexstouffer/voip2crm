"""Webhook receiver: telephony provider -> pipeline.

A provider POSTs when a call recording is ready. We authenticate it, ack in
well under the provider's ~10s timeout, and hand the call to a single background
worker that downloads the audio, runs WhisperX, and pushes to the CRM. One
worker means transcription is serialized (friendly to a single GPU) and the
SQLite dedupe stays coherent.
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, abort, request

from ..config import Config
from ..models import CallRecord
from ..pipeline import Pipeline
from .base import InboundCall, ProviderAdapter, build_provider

log = logging.getLogger("voip2crm.webhook")


def _archive_recording(provider: ProviderAdapter, pipeline: Pipeline, call: InboundCall) -> Optional[str]:
    """Download the recording into the persistent archive (for sales review).
    No transcription — just store it. Returns the saved path."""
    path = provider.download(call, pipeline.recordings_dir)
    if path:
        log.info("archived recording for call %s -> %s", call.call_id, path)
    return path


def _to_record(provider: ProviderAdapter, pipeline: Pipeline, call: InboundCall,
               recording_ref: Optional[str] = None) -> Optional[CallRecord]:
    # Provider already gave us a transcript (Quo AI) — no download / WhisperX.
    if call.transcript is not None:
        return CallRecord(
            message_id=call.call_id,
            received_at=call.started_at,
            subject=f"{call.direction} call",
            caller_phone=call.counterparty(),
            transcript=call.transcript,
            recording_ref=recording_ref,
        )
    # Recording event in "transcribe" mode: download to the working dir, WhisperX runs.
    audio = provider.download(call, pipeline.audio_dir)
    if not audio:
        log.warning("no recording downloaded for call %s", call.call_id)
        return None
    return CallRecord(
        message_id=call.call_id,
        received_at=call.started_at,
        subject=f"{call.direction} call",
        caller_phone=call.counterparty(),
        audio_path=audio,
        recording_ref=recording_ref,
    )


def _worker(pipeline: Pipeline, provider: ProviderAdapter, cfg: dict,
            q: "queue.Queue[InboundCall]") -> None:
    recording_mode = (cfg.get("recording_mode") or "transcribe").lower()
    base_url = (cfg.get("recordings_base_url") or "").rstrip("/")

    while True:
        call = q.get()
        try:
            is_transcript = call.transcript is not None
            kind = "transcript" if is_transcript else "recording"
            key = f"{call.call_id}:{kind}"      # dedupe per event kind, so both run
            if pipeline.state.seen(key):
                log.info("duplicate %s for call %s; skipping", kind, call.call_id)
                continue

            if not is_transcript and recording_mode == "archive":
                # Store the audio for sales review; the note comes from the transcript event.
                _archive_recording(provider, pipeline, call)
                pipeline.state.mark(key)
                continue
            if not is_transcript and recording_mode == "off":
                pipeline.state.mark(key)
                continue

            # Note-producing path (transcript event, or recording event in transcribe mode).
            ref = None
            if base_url:
                ref = f"{base_url}/{call.call_id}.mp3"     # deterministic archive name
            log.info("processing %s call %s (%s)", call.direction, call.call_id,
                     call.counterparty() or "unknown number")
            rec = _to_record(provider, pipeline, call, recording_ref=ref)
            if rec is not None:
                pipeline.process_record(rec)
                pipeline.state.mark(key)
        except Exception:
            log.exception("worker failed on call %s", call.call_id)
        finally:
            q.task_done()


def create_app(pipeline: Pipeline, webhook_cfg: dict) -> Flask:
    app = Flask(__name__)
    provider = build_provider(webhook_cfg)
    path = webhook_cfg.get("path", "/webhook")
    token = webhook_cfg.get("shared_token") or None
    enforce_sig = bool(webhook_cfg.get("verify_signatures", False))

    work: "queue.Queue[InboundCall]" = queue.Queue()
    threading.Thread(target=_worker, args=(pipeline, provider, webhook_cfg, work), daemon=True).start()

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post(path)
    def hook():
        if token and request.args.get("token") != token:
            abort(403)
        if not provider.verify(request):
            log.warning("signature verification failed")
            if enforce_sig:
                abort(403)
        try:
            call = provider.parse(request)
        except Exception:
            log.exception("failed to parse webhook")
            return {"error": "bad payload"}, 400
        if call is None:
            return {"ignored": True}, 200          # not a recording-ready event
        work.put(call)                              # ack fast; process off-thread
        return {"queued": call.call_id}, 200

    return app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="voip2crm telephony webhook receiver")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--no-transcribe", action="store_true",
                   help="skip WhisperX (validate the plumbing before WhisperX is ready)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config.load(args.config)
    pipeline = Pipeline(cfg, skip_transcribe=args.no_transcribe)
    wcfg = cfg.section("webhook")
    app = create_app(pipeline, wcfg)
    host = wcfg.get("host", "0.0.0.0")
    port = int(wcfg.get("port", 8080))
    log.info("webhook receiver listening on %s:%s%s (provider=%s)",
             host, port, wcfg.get("path", "/webhook"), wcfg.get("provider", "openphone"))
    app.run(host=host, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
