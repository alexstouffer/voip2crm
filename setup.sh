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

Setup complete. Next steps:
  1. Edit .env         -> TWENTY_API_KEY (and any optional keys)
  2. Edit config.yaml  -> Twenty base_url, whisperx device/model, gmail query
  3. Drop your Google OAuth client secret here as credentials.json
  4. Ensure WhisperX is available in this venv:
       pip install whisperx        # or: pip install -e '.[transcribe]'
  5. First run (opens a browser to authorize Gmail, caches token.json):
       source .venv/bin/activate
       python run.py --once --no-transcribe --limit 3 -v

Then schedule it — see HOMELAB.md (systemd timer or cron).
NOTE
