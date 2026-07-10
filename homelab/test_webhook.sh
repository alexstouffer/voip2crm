#!/usr/bin/env bash
# Fire a synthetic OpenPhone call.recording.completed at a running receiver.
# Serves a short generated audio clip on localhost so the download step succeeds,
# letting you validate receiver -> parse -> download -> (transcribe) -> CRM
# without making a real phone call.
#
#   RECEIVER=http://localhost:8080/webhook WEBHOOK_TOKEN=... ./homelab/test_webhook.sh
#
# Tip: start the receiver with --no-transcribe first to test pure plumbing, and
# point crm.provider at "local" (or run against Twenty once plumbing is green).
set -euo pipefail
cd "$(dirname "$0")/.."

RECEIVER="${RECEIVER:-http://localhost:8080/webhook}"
TOKEN="${WEBHOOK_TOKEN:-}"
PORT="${AUDIO_PORT:-8899}"

mkdir -p data/test
CLIP="data/test/sample.wav"
if [ ! -f "$CLIP" ]; then
  command -v ffmpeg >/dev/null || { echo "need ffmpeg to generate a clip, or set AUDIO_URL"; exit 1; }
  ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -ac 1 -ar 16000 "$CLIP" -y >/dev/null 2>&1
fi

# Serve the clip locally so the adapter can fetch it.
( cd data/test && python -m http.server "$PORT" >/dev/null 2>&1 ) &
SRV=$!
trap "kill $SRV 2>/dev/null || true" EXIT
sleep 1

AUDIO_URL="${AUDIO_URL:-http://localhost:$PORT/sample.wav}"
URL="$RECEIVER"; [ -n "$TOKEN" ] && URL="$RECEIVER?token=$TOKEN"
BODY="$(sed "s#__AUDIO_URL__#$AUDIO_URL#" examples/openphone_recording.json)"

echo "POST -> $URL"
curl -sS -X POST "$URL" -H "Content-Type: application/json" -d "$BODY"; echo
echo
echo "Now check the receiver logs, then your CRM (or data/crm_local.sqlite in dry-run)."
