# Webhook ingestion (telephony provider → Twenty)

This path captures **actual calls** — inbound and outbound — instead of
voicemails. Your phone provider records each call and POSTs a webhook when the
recording is ready; the receiver downloads it, runs WhisperX, and pushes a note
(plus a follow-up task) to Twenty. The transcription/CRM half is identical to the
Gmail path; only ingestion changes.

```
Call ends → provider records it → webhook POST → receiver:
   verify → download recording → WhisperX → follow-up extract → Twenty CRM
```

Set `source: webhook` in `config.yaml` and pick `webhook.provider`.

## Why a provider instead of Google Voice

Google Voice has no call API and only exposes automatic recordings through Vault
eDiscovery exports (Premier + a Vault-capable Workspace edition, ~$57/user/mo).
A provider built for this hands you the recording over a webhook the moment the
call ends — simpler and cheaper. You can port your existing number.

## Recommended: OpenPhone / Quo

A real business phone app with per-call webhooks.

1. Turn on call recording (and auto-record for inbound + outbound) in OpenPhone
   settings. Recording plays an announcement — keep it on (see consent below).
2. Create a webhook for the **`call.recording.completed`** event, pointing at
   your receiver's public URL (see "Exposing the receiver"):
   `https://<your-tunnel-host>/webhook?token=<WEBHOOK_TOKEN>`
   Do this in the app (Settings → Integrations → Webhooks) or via the API:
   ```bash
   curl -X POST https://api.openphone.com/v1/webhooks/calls \
     -H "Authorization: Bearer $OPENPHONE_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"url":"https://<host>/webhook?token=<WEBHOOK_TOKEN>",
          "events":["call.recording.completed"]}'
   ```
3. Put the webhook's signing secret in `OPENPHONE_SIGNING_SECRET` and, once
   you've confirmed the signature format against a real event, set
   `webhook.verify_signatures: true`.

The payload delivers the recording URL in `data.object.media[].url`; the adapter
downloads it directly (no auth needed on those URLs).

## Alternative: Twilio (cheapest, most control)

Best if you want per-minute pricing and to build your own call flow. Configure
each call's `recordingStatusCallback` to
`https://<host>/webhook?token=<WEBHOOK_TOKEN>`, set `webhook.provider: twilio`,
and fill in `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN`. The recording callback
omits From/To, so the adapter fetches the Call resource to fill them in.
Behind a tunnel, set `webhook.twilio.public_url` so signature checks match.

## Exposing the receiver

The provider needs a public HTTPS URL, but your home lab doesn't need an open
port. Easiest options:

- **Cloudflare Tunnel** (free): `cloudflared tunnel --url http://localhost:8080`
  gives you an HTTPS hostname that forwards to the receiver. For a stable
  hostname, create a named tunnel.
- **Tailscale Funnel**: exposes a single service over HTTPS on your tailnet.
- **Reverse proxy** (Caddy/nginx) with a real cert if you already port-forward.

Keep the `?token=` shared secret on the URL regardless — it's simple and blocks
random internet noise even before signature verification.

## Run it

```bash
source .venv/bin/activate
pip install -r requirements.txt          # adds Flask
python serve.py --config config.yaml -v  # listens on :8080 by default
# health check:
curl localhost:8080/healthz
```

Install as a service:

```bash
sudo cp homelab/voip2crm-webhook.service /etc/systemd/system/   # edit user/paths
sudo systemctl daemon-reload && sudo systemctl enable --now voip2crm-webhook
journalctl -u voip2crm-webhook -f
```

The receiver acks each webhook immediately (providers retry if you take more than
~10s) and processes the call on a single background worker, so WhisperX runs one
call at a time — friendly to a single GPU. Duplicate deliveries are dropped via
the call id in the state db.

For a multi-process setup, run under gunicorn with a single worker so the queue
and dedupe stay coherent:
`gunicorn -w 1 -b 0.0.0.0:8080 'voip2crm.webhook.server:create_app(...)'` — or just
keep the single-process `serve.py`, which is plenty for call volumes here.

## Provider transcripts (optional)

OpenPhone can also emit `call.transcript.completed`. If you'd rather skip
WhisperX and use the provider's transcript, that's a small adapter change — say
the word. Default here is WhisperX so the transcript stays on your hardware.

## Consent

You're mostly recording outbound calls, and California is all-party consent. Keep
the provider's recording announcement enabled — that's what satisfies consent.
This is handled at the provider, upstream of this pipeline. Not legal advice.
