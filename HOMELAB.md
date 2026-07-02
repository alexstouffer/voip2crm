# Home lab deployment

The home lab is the cheapest option, full stop — you already own the compute, so
there's no per-call or idle billing, and none of the AWS push/Pub/Sub/API-Gateway
machinery is needed. You can ignore the `aws/` folder entirely.

How batching works here: each scheduled run calls `run.py --once`, which sweeps
**every** Google Voice voicemail that arrived since the last run and pushes them
all to Twenty in that pass. Running a few times a day across business hours just
means each sweep covers a few hours of accumulated calls. The Gmail processed
label (`gmail.processed_label`) guarantees nothing is handled twice, so overlap
between the lookback window and prior runs is harmless. Keep `gmail.lookback_days`
comfortably larger than your longest gap between runs (the default 7 covers a
weekend fine).

## One-time setup

```bash
cd /home/youruser/voip2crm_pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # WhisperX already installed on this box
cp config.example.yaml config.yaml     # fill in Twenty base_url + TWENTY_API_KEY
# First run authorizes Gmail in a browser and caches token.json:
python run.py --once --no-transcribe --limit 3 -v
```

If the lab has a GPU, you're no longer constrained by Lambda's CPU/time limits —
bump quality in `config.yaml`:

```yaml
whisperx:
  model: large-v3
  device: cuda
  compute_type: float16
```

## Schedule it — pick one

**systemd timer (recommended for a home lab).** `Persistent=true` catches up a
run if the box was off at trigger time, and logs land in journald.

```bash
sudo cp homelab/voip2crm.service /etc/systemd/system/
sudo cp homelab/voip2crm.timer   /etc/systemd/system/
# edit User= and the paths in both files first
sudo systemctl daemon-reload
sudo systemctl enable --now voip2crm.timer
systemctl list-timers voip2crm.timer        # confirm next run
journalctl -u voip2crm.service -f           # watch a run
```

**cron (simplest).**

```bash
crontab -e
# paste the contents of homelab/crontab.example, fixing the path
```

Both default to 9am / 12pm / 3pm / 6pm Pacific, Mon–Fri. Edit the hour list to
change cadence. Note the timezone caveats in each file (systemd ≥246 honors the
inline `America/Los_Angeles`; otherwise set the host timezone).

**Windows lab.** Use `homelab/run_batch.ps1` with Task Scheduler. Easiest is the
GUI: create a task, action = `powershell.exe -ExecutionPolicy Bypass -File
C:\path\voip2crm_pipeline\homelab\run_batch.ps1`, then add four daily triggers
(9/12/15/18) restricted to weekdays. Or one trigger to start, duplicated.

## What runs each time

`run_batch.sh` activates the venv, takes a `flock` so two runs can't collide,
runs the batch with a per-run cap (`BATCH_LIMIT`, default 50), appends to
`data/logs/batch-YYYYMMDD.log`, and prunes logs older than 30 days. Tune the cap
or schedule via env vars at the top of the script.

## If "notes in batches" meant something more

Right now it's one Twenty note (and, when warranted, one follow-up task) per
call, created during the batch sweep. If you instead want **one consolidated
digest note per run** (all of a window's calls in a single note), or want to use
**Twenty's batch API** (up to 60 records per request) to cut API round-trips on
busy sweeps, both are small additions — say the word and I'll wire whichever fits.
