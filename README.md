# gv-crm-pipeline

Turns Google Voice voicemails into Twenty CRM notes — transcribed with WhisperX,
with follow-up tasks created automatically when a call needs one. Built to run as
a scheduled batch job on a home lab (Linux), a few times a day.

## How it works

```
Google Voice  ->  Gmail (voicemail-to-email)  ->  this pipeline:
   poll Gmail  ->  download audio  ->  WhisperX  ->  detect follow-ups  ->  Twenty CRM
```

Google Voice has no official API for call audio, so the supported path is its
voicemail-to-email forwarding. Enable it in Google Voice:
**Settings -> Voicemail -> "Get voicemail via email."** If an email carries only
Google's text transcript and no audio attachment, the pipeline falls back to
that text (`--no-transcribe`).

Idempotency is handled by a Gmail label (`gmail.processed_label`): processed
messages get labeled and drop out of future scans, so the job is stateless and
safe to run on any schedule without double-posting.

## Quickstart

```bash
git clone <your-remote> gv-crm-pipeline && cd gv-crm-pipeline
./setup.sh                  # venv + deps + scaffolds config.yaml and .env
# edit .env and config.yaml, drop in credentials.json, then:
source .venv/bin/activate
python run.py --once --dry-run --no-transcribe --limit 3 -v   # smoke test
python run.py --once -v                                       # real run
```

`make help`-style targets are in the `Makefile` (`make setup`, `make dry`, `make run`).

## Configuration

Copy and edit the two local files (both gitignored):

- `config.yaml` (from `config.example.yaml`) — Gmail query, WhisperX model/device,
  follow-up keywords, CRM provider + Twenty `base_url`, alerts.
- `.env` (from `.env.example`) — secrets like `TWENTY_API_KEY`, referenced from
  `config.yaml` as `${TWENTY_API_KEY}`. Loaded automatically at startup.

CRM is pluggable. `crm.provider` can be `twenty` (default), `hubspot`, or `local`
(a SQLite stand-in for testing with no external CRM). To add another CRM,
implement three methods in `gv_crm/crm/` — see `gv_crm/crm/base.py`.

## Deploy

- **Home lab (recommended):** schedule a few batched runs a day with a systemd
  timer or cron. See **[HOMELAB.md](HOMELAB.md)**.
- **AWS (optional):** serverless/push variants and cost notes are in
  **[AWS_DEPLOY.md](AWS_DEPLOY.md)**. Not needed if you run on your own hardware.

## Layout

```
run.py                 launcher (same as the `gv-crm` console command)
gv_crm/
  cli.py               argument parsing / entry point
  pipeline.py          orchestration
  gmail_source.py      Gmail auth, message search, audio download, labels, watch
  transcribe.py        WhisperX wrapper
  extract.py           follow-up detection (rules + optional LLM)
  alerts.py            CSV log + optional self-email reminders
  state.py             SQLite dedupe (used when no processed_label is set)
  crm/                 base interface + twenty / hubspot / local adapters
homelab/               cron + systemd units + batch wrapper (run_batch.sh)
aws/                   Dockerfile + Lambda handlers (optional)
```

## A note on recording consent

Recording-consent law is upstream of this pipeline, which only processes
recordings that already exist. California is an all-party consent state; rules
vary elsewhere. Make sure the recording side in Google Voice meets the applicable
rules. Not legal advice.

## License

MIT — see [LICENSE](LICENSE).
