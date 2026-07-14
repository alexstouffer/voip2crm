# voip2crm

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

Turn **phone calls into CRM notes**. When a call ends, your VoIP provider posts a
webhook; voip2crm writes a transcript note to [Twenty CRM](https://twenty.com),
archives the audio for review, and creates a follow-up task when the caller asked
for one.

Transcription comes **from your VoIP provider by default** (Quo/OpenPhone AI
transcripts) — no GPU, no ML dependencies, nothing to install. Local
transcription with [WhisperX](https://github.com/m-bain/whisperX) is an optional
alternative.

---

## Contents

- [How it works](#how-it-works)
- [Transcription: provider vs local](#transcription-provider-vs-local)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Follow-up detection](#follow-up-detection)
- [CRM adapters](#crm-adapters)
- [Exposing the receiver](#exposing-the-receiver)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Recording consent](#recording-consent)
- [License](#license)

---

## How it works

```
Call ends
    |
    +-- call.transcript.completed --> transcript --> follow-up detection --> Twenty note + task
    |
    +-- call.recording.completed  --> audio archived to data/recordings/ (sales review)
```

Both webhooks fire for the same call and are handled as separate jobs: the
transcript writes the note, the recording gets stored. The receiver acks in
milliseconds (providers retry after ~10s) and does the work on a background queue.

An alternative **Gmail source** (`source: gmail`) polls Google Voice voicemail
emails in batches — see [HOMELAB.md](HOMELAB.md). It exists because Google Voice
has no call API; for real calls, use the webhook path above.

## Transcription: provider vs local

|  | Provider transcripts (default) | Local WhisperX |
|---|---|---|
| Subscribe to | `call.transcript.completed` | `call.recording.completed` |
| Config | `recording_mode: archive` | `recording_mode: transcribe` |
| Needs | Quo Business plan or higher | WhisperX + ffmpeg (GPU optional) |
| Setup | nothing to install | `./homelab/install_whisperx.sh` |
| Gives you | transcript in the payload, zero infra | diarization, word timestamps, fully offline |

**The default is provider transcripts.** Audio is archived either way, so you keep
recordings for sales review regardless of which transcriber you use.

To use WhisperX instead: set `webhook.recording_mode: transcribe`, subscribe to
`call.recording.completed` only, and run the installer.

## Features

- Webhook ingestion from **Quo/OpenPhone** or **Twilio**, inbound and outbound
- Transcript notes with no local ML — or WhisperX locally if you prefer
- **Audio archived** to `data/recordings/`; notes reference it for sales review
- Follow-up detection: keyword/date rules, plus an optional LLM pass
- Automatic follow-up **tasks** with due dates and priority
- Pluggable CRM adapters — **Twenty** (default), **HubSpot**, **local** (testing)
- Signature verification plus a shared-token check on the endpoint
- Per-event-kind dedupe, so duplicate deliveries never double-post
- Optional reminders: follow-up CSV log and self-email

## Requirements

- Linux, Python 3.10+
- A **Quo/OpenPhone** account (Business plan or higher for AI transcripts) with
  call recording enabled — or Twilio
- A reachable **Twenty CRM** instance and an API key (or another CRM adapter)
- A public HTTPS URL pointing at the receiver (e.g. a free Cloudflare Tunnel)
- *Only for local transcription:* WhisperX and ffmpeg

## Installation

```bash
git clone https://github.com/alexstouffer/voip2crm.git
cd voip2crm
./setup.sh          # venv, deps, and scaffolds config.yaml + .env
```

That's the entire install for the default setup — no WhisperX, no ffmpeg, no GPU.

Then create **both** webhooks in Quo pointing at your public URL, so you get the
transcript *and* the audio. The API calls, signing secret, and tunnel setup are in
**[WEBHOOK.md](WEBHOOK.md)**.

## Configuration

Two local files, both gitignored:

- **`config.yaml`** (from `config.example.yaml`) — behavior and non-secret settings.
- **`.env`** (from `.env.example`) — secrets, referenced as `${VAR}` and loaded
  automatically at startup.

| Section | What it controls |
|---|---|
| `source` | `webhook` (calls) or `gmail` (Google Voice voicemails) |
| `webhook` | provider, port/path, `shared_token`, `recording_mode`, signature checks |
| `webhook.openphone.my_numbers` | **your** Quo line(s) — used to pick the other party as the contact and to label speakers Agent vs Caller |
| `crm` | `twenty` / `hubspot` / `local`, plus Twenty `base_url` |
| `extract` | follow-up keywords and the optional LLM pass |
| `whisperx` | only used when `recording_mode: transcribe` |
| `storage` | `recordings_dir`, transcript dir, state db |

Phone-number formatting doesn't matter — `(657) 255-7214` and `+16572557214`
both match.

## Usage

```bash
source .venv/bin/activate
python serve.py -v                 # start the receiver (default :8080)
curl localhost:8080/healthz
```

Test without making a real call:

```bash
# transcript -> note (+ follow-up task; the sample asks for a callback)
curl -X POST "http://localhost:8080/webhook?token=$WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" -d @examples/openphone_transcript.json

# recording -> archived to data/recordings/
curl -X POST "http://localhost:8080/webhook?token=$WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" -d @examples/openphone_recording.json
```

Start with `crm.provider: local` (writes to `data/crm_local.sqlite`) to validate
the plumbing, then switch to `twenty`.

Run it as a service:

```bash
sudo cp homelab/voip2crm-webhook.service /etc/systemd/system/   # edit user/paths
sudo systemctl daemon-reload && sudo systemctl enable --now voip2crm-webhook
journalctl -u voip2crm-webhook -f
```

## Follow-up detection

Rule-based by default: matches `extract.followup_keywords` and parses phrases like
"call me tomorrow" or "by Friday" into a due date. Set `extract.use_llm: true`
with an `ANTHROPIC_API_KEY` for a structured pass that yields a cleaner summary
plus priority and due date, with the rules as fallback.

## CRM adapters

Set `crm.provider` to `twenty` (default), `hubspot`, or `local`. Adapters live in
`voip2crm/crm/` and implement three methods:

```python
class CRMAdapter:
    def upsert_contact(self, rec) -> str: ...
    def add_note(self, contact_id, rec) -> str: ...
    def create_followup_task(self, contact_id, title, due, body, priority) -> str: ...
```

The Twenty adapter attaches notes/tasks via Twenty's `noteTargets` / `taskTargets`
join objects and auto-adapts to the `body` vs `bodyV2` field difference between
versions.

## Exposing the receiver

The provider has to reach your box, but you don't need to open any ports. A
Cloudflare Tunnel makes an **outbound** connection and gives you a public HTTPS
hostname that forwards only the one route you configure — nothing else on your
network is exposed. Tailscale Funnel or a reverse proxy work too. See
[WEBHOOK.md](WEBHOOK.md).

## Project structure

```
serve.py               webhook receiver (the main entry point)
run.py                 batch launcher for the Gmail source
setup.sh               one-time bootstrap
config.example.yaml    copy to config.yaml
.env.example           copy to .env
voip2crm/
  webhook/             receiver + openphone / twilio adapters
  pipeline.py          orchestration (shared transcribe -> extract -> CRM path)
  extract.py           follow-up detection
  transcribe.py        WhisperX wrapper (only used in transcribe mode)
  gmail_source.py      Google Voice voicemail polling (alternative source)
  crm/                 base interface + twenty / hubspot / local adapters
examples/              sample webhook payloads for testing
homelab/               systemd units, batch wrapper, WhisperX installer
aws/                   optional serverless deploy
```

## Troubleshooting

- **Caller shows up as your own number** — set `webhook.openphone.my_numbers` to
  your Quo line so the receiver can tell which party is external.
- **Note created but no audio** — you're only subscribed to the transcript event.
  Add a `call.recording.completed` webhook and keep `recording_mode: archive`.
- **Audio archived but no note** — the reverse: add the transcript webhook.
- **Twenty returns 400 on note/task create** — the body field name differs by
  version. Set `crm.twenty.body_field` to `body` or `bodyV2` (check Settings ->
  API & Webhooks -> Playground).
- **Webhook returns 403** — the `?token=` doesn't match `WEBHOOK_TOKEN`, or
  signature verification is on and the signing secret is wrong.
- **Provider keeps retrying the same call** — it isn't getting an ack within ~10s.
  The receiver acks immediately, so look for a crash in the logs.

## Recording consent

Recording-consent law is upstream of this pipeline, which only processes
recordings that already exist. California is an all-party consent state; rules
vary elsewhere. Keep your provider's recording announcement enabled. This is not
legal advice.

## License

[MIT](LICENSE)
