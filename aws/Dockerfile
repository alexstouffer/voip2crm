# Lambda container image (arm64 / Graviton = ~20% cheaper than x86).
# WhisperX + a baked-in model so cold starts don't download weights.
FROM public.ecr.aws/lambda/python:3.12-arm64

# System deps: ffmpeg for audio decoding (WhisperX/ctranslate2 need it).
RUN dnf install -y tar gzip xz && \
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz \
      -o /tmp/ffmpeg.tar.xz && \
    mkdir -p /tmp/ffmpeg && tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1 && \
    cp /tmp/ffmpeg/ffmpeg /usr/local/bin/ && cp /tmp/ffmpeg/ffprobe /usr/local/bin/ && \
    rm -rf /tmp/ffmpeg* && dnf clean all

# Python deps. Pin CPU torch to keep the image small (no CUDA on Lambda).
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir whisperx && \
    pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Pre-download the WhisperX/faster-whisper model into the image so the first
# real invocation is fast. Pick a small model for CPU; change as needed.
ENV WHISPER_MODEL=small
RUN python -c "import whisperx; whisperx.load_model('${WHISPER_MODEL}', 'cpu', compute_type='int8')"

# App code.
COPY gv_crm ${LAMBDA_TASK_ROOT}/gv_crm
COPY aws/lambda_handler.py ${LAMBDA_TASK_ROOT}/
COPY config.yaml ${LAMBDA_TASK_ROOT}/

# Default handler; override per-function in AWS (e.g. lambda_handler.push_handler).
CMD ["lambda_handler.scheduled_handler"]
