#!/usr/bin/env bash
# One-time setup on a fresh clone (Linux).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "[*] Creating virtualenv (.venv)…"
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[*] Installing dependencies…"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[*] Preparing local config & env…"
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env

cat <<'NOTE'

Setup complete. Default path is VoIP webhooks (Quo/OpenPhone) -> Twenty.

Next steps:
  1. Edit .env:
       TWENTY_API_KEY            (Twenty: Settings -> API & Webhooks)
       WEBHOOK_TOKEN             (any long random string)
       OPENPHONE_SIGNING_SECRET  (Quo: the webhook's "Reveal Signing Secret")
  2. Edit config.yaml:
       crm.twenty.base_url       (your Twenty URL, ending in /rest)
       crm.provider: local       (for the first test; switch to twenty after)
     Your Quo number and recording_mode: archive are already set.
  3. Smoke test (no phone call needed):
       source .venv/bin/activate
       python serve.py -v
       # in another terminal:
       curl -X POST "http://localhost:8080/webhook?token=$WEBHOOK_TOKEN" \
         -H "Content-Type: application/json" -d @examples/openphone_transcript.json
     Expect a note + follow-up in data/crm_local.sqlite.
  4. Create the Quo webhooks and expose the receiver: see WEBHOOK.md.

Using Quo's AI transcripts means no WhisperX, ffmpeg, or Google setup.
(For the alternate Google Voice voicemail path instead, see HOMELAB.md.)
NOTE
