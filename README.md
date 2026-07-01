# gv-crm-pipeline

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

Turn **Google Voice voicemails into Twenty CRM notes** — automatically transcribed
with [WhisperX](https://github.com/m-bain/whisperX), with follow-up tasks created
when a call needs one. Designed to run as a scheduled batch job on a home lab, a
few times a day.

---

## Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Follow-up detection](#follow-up-detection)
- [CRM adapters](#crm-adapters)
- [Scheduling on a home lab](#scheduling-on-a-home-lab)
- [Deploying to AWS (optional)](#deploying-to-aws-optional)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Recording consent](#recording-consent)
- [License](#license)

---

## How it works

```
Google Voice
    │  (voicemail-to-email forwarding)
    ▼
  Gmail ──▶ poll ──▶ download audio ──▶ WhisperX ──▶ detect follow-ups ──▶ Twenty CRM
                                                                              │
                                                                    note + follow-up task
```

Google Voice has **no official API for call audio**, so the supported path is its
built-in voicemail-to-email forwarding. Enable it in Google Voice under
**Settings → Voicemail → \"Get voicemail via email.\"** Each run polls Gmail for
those messages, downloads the audio attachment, transcribes it, extracts any
follow-up, and writes the result to your CRM.

If a message carries only Google's own text transcript and no audio attachment,
the pipeline falls back to that text automatically.

**Idempotency is stateless.** Processed messages get a Gmail label
(`gmail.processed_label`) and drop out of future scans, so the job is safe to run
on any schedule without ever double-posting — no database required.

## Features

- Gmail ingestion with OAuth, attachment download, and label-based dedupe
- WhisperX transcription with optional word-alignment and speaker diarization
- Follow-up detection: fast keyword/date rules, plus an optional LLM pass for
  cleaner summaries and structured fields
- Pluggable CRM adapters — **Twenty** (default), **HubSpot**, and a **local**
  SQLite stand-in for testing with no external CRM
- Automatic follow-up **task** creation with due dates and priority
- Optional reminders: append to a CSV and/or email yourself via Gmail
- Batch-friendly: one `--once` run sweeps every voicemail since the last run
- Ships with home-lab scheduling (systemd timer + cron) and an optional AWS path

## Requirements

- Linux, Python 3.10+
- A Google account receiving Google Voice voicemail emails
- A Google Cloud project with the **Gmail API** enabled and a **Desktop app**
  OAuth client
- [WhisperX](https://github.com/m-bain/whisperX) (installed separately; a GPU is
  optional but much faster)
- A reachable **Twenty CRM** instance and an API key (or use another adapter)

## Installation

```bash
git clone <your-remote> gv-crm-pipeline
cd gv-crm-pipeline
./setup.sh          # creates .venv, installs deps, scaffolds config.yaml and .env
```

Then wire up Gmail access:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and **enable the Gmail API**.
2. Create an **OAuth client ID** of type **Desktop app**; download the JSON and
   save it in the repo root as `credentials.json`.
3. Make sure WhisperX is importable in the venv:
   ```bash
   source .venv/bin/activate
   pip install whisperx        # or: pip install -e '.[transcribe]'
   ```
4. First run opens a browser to authorize and caches `token.json`:
   ```bash
   python run.py --once --no-transcribe --limit 3 -v
   ```

## Configuration

Two local files, both gitignored:

- **`config.yaml`** (from `config.example.yaml`) — behavior and non-secret settings.
- **`.env`** (from `.env.example`) — secrets, referenced in `config.yaml` as
  `${VAR}` and loaded automatically at startup.

Key sections of `config.yaml`:

| Section     | What it controls                                                        |
|-------------|-------------------------------------------------------------------------|
| `gmail`     | OAuth paths, search `query`, `processed_label`, `lookback_days`          |
| `whisperx`  | `model`, `device` (`cpu`/`cuda`), `compute_type`, diarization           |
| `extract`   | follow-up `keywords`, and the optional LLM pass (`use_llm`)             |
| `crm`       | `provider` (`twenty` / `hubspot` / `local`) and per-provider settings   |
| `alerts`    | follow-up CSV log and optional self-email                               |
| `storage`   | audio / transcript / state-db paths                                     |

For a GPU home lab, raise quality:

```yaml
whisperx:
  model: large-v3
  device: cuda
  compute_type: float16
```

## Usage

```bash
source .venv/bin/activate

python run.py --once -v                          # process new voicemails, then exit
python run.py --once --dry-run --no-transcribe   # end-to-end test, local CRM, no ML
python run.py --once --limit 10                  # cap messages per run
python run.py --once --reprocess                 # ignore dedupe, redo everything
python run.py --watch --interval 300             # poll every 5 min (dev only)
```

Equivalent `make` targets: `make setup`, `make dry`, `make run`, `make watch`.
After `pip install -e .`, the same launcher is available as the `gv-crm` command.

## Follow-up detection

Rule-based detection always runs: it matches `extract.followup_keywords` and
parses phrases like \"call me tomorrow\" or \"by Friday\" into a due date. Enable
`extract.use_llm: true` with an `ANTHROPIC_API_KEY` for a single structured-JSON
pass that produces a cleaner summary plus contact/priority/due-date fields, with
the rule-based result as a fallback.

## CRM adapters

Set `crm.provider` to `twenty` (default), `hubspot`, or `local`. Adapters live in
`gv_crm/crm/` and implement three methods:

```python
class CRMAdapter:
    def upsert_contact(self, rec) -> str: ...
    def add_note(self, contact_id, rec) -> str: ...
    def create_followup_task(self, contact_id, title, due, body, priority) -> str: ...
```

To add a CRM, copy `local.py`, implement the three methods, and register it in
`crm/base.py`. The **Twenty** adapter attaches notes/tasks through Twenty's
`noteTargets` / `taskTargets` join objects and auto-adapts to the `body` vs
`bodyV2` field difference between versions.

## Scheduling on a home lab

Run a few batched sweeps a day with a **systemd timer** (recommended — catches up
missed runs) or **cron**. Units and a locking batch wrapper are in `homelab/`.
Full instructions: **[HOMELAB.md](HOMELAB.md)**.

```bash
sudo cp homelab/gv-crm.{service,timer} /etc/systemd/system/   # edit paths/user first
sudo systemctl daemon-reload && sudo systemctl enable --now gv-crm.timer
```

## Deploying to AWS (optional)

Not needed if you run on your own hardware. If you want a serverless/push
deployment, cost-optimized options (Lambda + Pub/Sub, or Fargate for long calls)
and a Dockerfile are in **[AWS_DEPLOY.md](AWS_DEPLOY.md)** and `aws/`.

## Project structure

```
run.py                 launcher (same as the installed `gv-crm` command)
setup.sh               one-time bootstrap
Makefile               setup / dry / run / watch targets
config.example.yaml    copy to config.yaml
.env.example           copy to .env
gv_crm/
  cli.py               argument parsing / entry point
  pipeline.py          orchestration
  gmail_source.py      Gmail auth, search, audio download, labels, watch
  transcribe.py        WhisperX wrapper
  extract.py           follow-up detection (rules + optional LLM)
  alerts.py            CSV log + optional self-email
  state.py             SQLite dedupe (when processed_label is unset)
  models.py            the CallRecord that flows through the pipeline
  crm/                 base interface + twenty / hubspot / local adapters
homelab/               systemd units, crontab, batch wrapper
aws/                   Dockerfile + Lambda handlers (optional)
```

## Troubleshooting

- **No audio, only text in the note** — your Google Voice emails aren't attaching
  audio. Confirm voicemail-to-email is on; the pipeline uses Google's text
  transcript as a fallback in the meantime.
- **Twenty returns 400 on note/task create** — the body field name differs by
  version. Set `crm.twenty.body_field` to `body` or `bodyV2` (check Settings →
  API & Webhooks → Playground). The adapter also auto-retries the other shape.
- **Gmail auth loops or fails** — delete `token.json` and re-run to re-authorize;
  make sure the OAuth client is a **Desktop app** type.
- **Schedule runs at the wrong time** — cron uses the host timezone; set
  `CRON_TZ` (see `homelab/crontab.example`) or the systemd `OnCalendar` timezone.
- **Nothing gets processed** — messages may already carry the processed label.
  Run with `--reprocess` to force, or widen `gmail.query`.

## Roadmap

- Optional consolidated **digest note** per batch (vs one note per call)
- **Twenty batch API** writes to cut round-trips on busy sweeps
- OIDC verification on the AWS push endpoint

## Recording consent

Recording-consent law is upstream of this pipeline, which only processes
recordings that already exist. California is an all-party consent state; rules
vary elsewhere. Ensure the recording side in Google Voice meets the applicable
rules. This is not legal advice.

## License

[MIT](LICENSE)
