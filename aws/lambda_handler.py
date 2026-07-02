"""AWS Lambda entry points.

Three handlers, pick based on your architecture (see AWS_DEPLOY.md):

  scheduled_handler  -- EventBridge timer -> poll Gmail -> process. Simplest/cheapest.
  push_handler       -- API Gateway <- Pub/Sub push. Real-time trigger; it just
                        runs the same scoped scan (label-dedupe makes that safe),
                        so no history cursor / state store is needed.
  renew_watch_handler-- Daily EventBridge timer -> refresh the Gmail watch
                        (watches expire within 7 days). Only for the push path.

Idempotency is via the Gmail processed label (config gmail.processed_label), so
these are safe on ephemeral Lambda with no external state store.

Cold-start helpers hydrate the Gmail OAuth token from SSM if GMAIL_TOKEN_SSM is
set, so you don't bake secrets into the image.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("voip2crm.aws")

_PIPELINE = None  # reused across warm invocations


def _hydrate_token() -> None:
    """If GMAIL_TOKEN_SSM is set, write the SSM SecureString to the configured
    token path before the pipeline authenticates."""
    ssm_name = os.environ.get("GMAIL_TOKEN_SSM")
    if not ssm_name:
        return
    import boto3

    token_path = os.environ.get("GMAIL_TOKEN_PATH", "/tmp/token.json")
    val = boto3.client("ssm").get_parameter(Name=ssm_name, WithDecryption=True)
    Path(token_path).write_text(val["Parameter"]["Value"], encoding="utf-8")


def _get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        from voip2crm.config import Config
        from voip2crm.pipeline import Pipeline

        _hydrate_token()
        cfg = Config.load(os.environ.get("CONFIG_PATH", "config.yaml"))
        _PIPELINE = Pipeline(cfg)
    return _PIPELINE


# --- handlers --------------------------------------------------------------

def scheduled_handler(event, context):
    limit = int(os.environ.get("BATCH_LIMIT", "25"))
    n = _get_pipeline().run_once(limit=limit)
    return {"processed": n}


def push_handler(event, context):
    """API Gateway (HTTP API) proxy event carrying a Pub/Sub push.
    Always returns 200 fast so Pub/Sub doesn't retry-storm; work is best-effort."""
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        envelope = json.loads(body)
        # The Pub/Sub message payload (emailAddress + historyId) isn't needed:
        # label-dedupe means we can just rescan the scoped query safely.
        log.info("Push received: %s", envelope.get("message", {}).get("messageId"))
        limit = int(os.environ.get("BATCH_LIMIT", "25"))
        n = _get_pipeline().run_once(limit=limit)
        return {"statusCode": 200, "body": json.dumps({"processed": n})}
    except Exception:
        log.exception("push_handler error (acking anyway)")
        return {"statusCode": 200, "body": "ack"}


def renew_watch_handler(event, context):
    """Refresh the Gmail watch so push keeps flowing. Run daily.
    Requires PUBSUB_TOPIC = projects/<proj>/topics/<topic>."""
    topic = os.environ["PUBSUB_TOPIC"]
    pipe = _get_pipeline()
    label_ids = [pipe.processed_label_id] if False else None  # watch all; dedupe downstream
    resp = pipe.gmail.start_watch(topic, label_ids=label_ids)
    log.info("watch refreshed: historyId=%s expiration=%s",
             resp.get("historyId"), resp.get("expiration"))
    return {"historyId": resp.get("historyId"), "expiration": resp.get("expiration")}
