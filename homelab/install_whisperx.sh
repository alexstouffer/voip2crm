#!/usr/bin/env bash
# Install WhisperX into this repo's .venv. Auto-detects an NVIDIA GPU and picks
# the least-painful build. CPU-only is the default when no GPU is found and is
# the fastest route to a working system; switch to GPU later by re-running on a
# CUDA box and updating config.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

VENV="${VENV:-.venv}"
[ -d "$VENV" ] || { echo "No $VENV found — run ./setup.sh first."; exit 1; }
# shellcheck disable=SC1091
source "$VENV/bin/activate"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "!! ffmpeg not found (WhisperX needs it). Install it, then re-run:"
  echo "     sudo apt-get install -y ffmpeg     # Debian/Ubuntu"
  echo "     sudo dnf install -y ffmpeg         # Fedora/RHEL"
  exit 1
fi

pip install --upgrade pip >/dev/null

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[*] NVIDIA GPU detected:"
  nvidia-smi -L || true
  echo "[*] Installing WhisperX with CUDA support..."
  pip install whisperx          # pulls a CUDA torch build + bundled cuDNN 9
  DEVICE=cuda; COMPUTE=float16; MODEL=large-v3
else
  echo "[*] No GPU detected — installing CPU-only (simplest, most reliable)."
  # Install CPU torch first so pip doesn't drag in the huge CUDA wheel.
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  pip install whisperx
  DEVICE=cpu; COMPUTE=int8; MODEL=small
fi

# On GPU, help ctranslate2 (faster-whisper backend) find pip-installed cuDNN/cuBLAS.
LD_HINT=""
if [ "$DEVICE" = "cuda" ]; then
  LD_HINT="$(python -c 'import os,nvidia.cublas.lib,nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__)+":"+os.path.dirname(nvidia.cudnn.lib.__file__))' 2>/dev/null || true)"
  [ -n "$LD_HINT" ] && export LD_LIBRARY_PATH="$LD_HINT:${LD_LIBRARY_PATH:-}"
fi

echo "[*] Verifying WhisperX loads on $DEVICE (model=base)..."
python - <<PY
import whisperx
whisperx.load_model("base", "${DEVICE}", compute_type="${COMPUTE}")
print("OK — WhisperX loaded on ${DEVICE}")
PY

cat <<NOTE

Done. Set these in config.yaml under whisperx:
    device: ${DEVICE}
    compute_type: ${COMPUTE}
    model: ${MODEL}        # adjust to taste (bigger = slower but more accurate)
NOTE

if [ "$DEVICE" = "cuda" ] && [ -n "$LD_HINT" ]; then
  cat <<GPU

GPU runtime note: if the service later can't find cuDNN, add this to
homelab/voip2crm-webhook.service under [Service]:
    Environment=LD_LIBRARY_PATH=${LD_HINT}
GPU
fi
