# AWS deployment — cheapest-first

WhisperX is the only expensive part, so the whole cost game is: **run compute
only when a call actually arrives, and run it on arm64.** Everything else
(Gmail trigger, glue) is pennies or free-tier.

Idempotency is handled by a Gmail label (`gmail.processed_label`), so none of
these designs need a database. That's what keeps the serverless options cheap.

## Pick an architecture

**A — Push (cheapest + real-time).** Gmail `watch` publishes to Pub/Sub on new
mail; Pub/Sub pushes to API Gateway; a worker Lambda (arm64, WhisperX) runs only
then. Idle cost ≈ $0 — you pay per call. Moving parts: a Pub/Sub topic, a daily
"renew watch" Lambda, an HTTP API. Rough cost at low volume: a few cents to ~$1–2/mo
plus ECR image storage (~$0.30/mo). This is the one to use if "cheap" is the goal.

**B — Scheduled (simplest).** One Lambda on an EventBridge timer (e.g. every 3–5
min) runs `run_once`. No Pub/Sub, no watch renewal. Downside: the function is
sized for WhisperX (high memory), so even empty polls cost a little — roughly
$2–4/mo of idle billing at a 5-min cadence, plus per-call compute. Trim it with
the poller/worker split below if that bugs you.

**C — Long calls / large model.** Lambda caps at 15 min and has no GPU. If your
recordings run long or you want `large-v2`, keep the push trigger but make the
worker a **Fargate task** (no time cap) — the trigger Lambda calls `ecs.run_task`.
Fargate arm64 at 2 vCPU/4 GB for ~5 min ≈ $0.01/call.

Cheapest-of-all if you don't actually need it on AWS: run `python run.py --watch`
(or a cron job) on the box already hosting Twenty CRM. Zero extra compute cost.

## Build & push the image (arm64)

```bash
cp config.example.yaml config.yaml   # fill in Twenty + Gmail settings
aws ecr create-repository --repository-name gv-crm
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# Build for arm64 (Graviton) and push.
docker buildx build --platform linux/arm64 -f aws/Dockerfile \
  -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/gv-crm:latest --push .
```

## Secrets (cheap): SSM, not Secrets Manager

Standard SSM parameters are free; Secrets Manager is ~$0.40/secret/mo. Store the
Gmail token (do the first OAuth locally to generate `token.json`, then upload it):

```bash
aws ssm put-parameter --name /gv-crm/gmail-token --type SecureString \
  --value file://token.json
aws ssm put-parameter --name /gv-crm/twenty-api-key --type SecureString --value "<key>"
```

Set the worker Lambda env: `GMAIL_TOKEN_SSM=/gv-crm/gmail-token`,
`GMAIL_TOKEN_PATH=/tmp/token.json`, `TWENTY_API_KEY` (or read it from SSM too),
`CONFIG_PATH=config.yaml`. In `config.yaml` point `gmail.token_path` at
`/tmp/token.json`. Give the Lambda role `ssm:GetParameter` + `kms:Decrypt`.

## Architecture A — push setup

1. **Worker Lambda** from the image; handler `lambda_handler.push_handler`;
   memory 4096–8192 MB; timeout 300 s; ephemeral storage 2048 MB; arch arm64.
2. **HTTP API** (API Gateway) with one route `POST /gmail` → the worker Lambda.
3. **Pub/Sub topic** in your Google Cloud project, and grant Gmail permission to
   publish:
   ```bash
   gcloud pubsub topics create gmail-gv
   gcloud pubsub topics add-iam-policy-binding gmail-gv \
     --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
     --role=roles/pubsub.publisher
   ```
4. **Push subscription** to your API Gateway URL:
   ```bash
   gcloud pubsub subscriptions create gmail-gv-sub \
     --topic=gmail-gv --push-endpoint=https://<api-id>.execute-api.<region>.amazonaws.com/gmail
   ```
   (For production, enable OIDC auth on the push subscription and verify the JWT
   in `push_handler` before processing.)
5. **Renew-watch Lambda** from the same image; handler
   `lambda_handler.renew_watch_handler`; env `PUBSUB_TOPIC=projects/<proj>/topics/gmail-gv`.
   Trigger it with an **EventBridge schedule** `rate(1 day)` — Gmail watches
   expire within 7 days. Run it once manually first to start the watch.

That's it: new voicemail → Pub/Sub → API Gateway → worker runs WhisperX → Twenty.

## Architecture B — scheduled setup

1. Worker Lambda from the image; handler `lambda_handler.scheduled_handler`; same
   sizing as above.
2. EventBridge schedule `rate(5 minutes)` → the Lambda. Done.

Optional poller/worker split to cut idle cost: a tiny 256 MB Lambda on the
schedule that only lists Gmail and, if anything matches, `lambda.invoke`s the big
worker. Idle drops to roughly $0.05/mo; WhisperX runs only on real calls.

## The zero-ops alternative worth knowing

If self-hosting WhisperX ever feels like more ops than it's worth, **AWS
Transcribe** removes the container entirely: ~$0.024/min of audio, no GPU, no
image. At, say, 30 calls/mo × 2 min that's ~$1.4/mo. You'd swap `transcribe.py`
for a Transcribe call (S3 upload → start job → poll). You already have WhisperX,
so this is just a fallback to keep in your back pocket.

## Verify before going live

```bash
# Local smoke test against Twenty, no transcription, capped at 3 messages:
python run.py --once --no-transcribe --limit 3 -v
# Then a real local run (transcribes) before deploying the image:
python run.py --once -v
```

Reminder (also in the README): recording-consent law is upstream of this
pipeline. California is all-party consent — make sure the recording side in
Google Voice meets the applicable rules. Not legal advice.
