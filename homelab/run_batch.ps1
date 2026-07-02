# Windows home lab batch runner (Task Scheduler).
# Mirrors run_batch.sh: single-instance guard + logging.
$ErrorActionPreference = "Stop"

# Project root = parent of this homelab\ folder.
$AppDir = Split-Path $PSScriptRoot -Parent
Set-Location $AppDir

$BatchLimit = if ($env:BATCH_LIMIT) { $env:BATCH_LIMIT } else { "50" }
$LogDir = Join-Path $AppDir "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("batch-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

# Single-instance guard via a named mutex.
$mutex = New-Object System.Threading.Mutex($false, "Global\voip2crm-batch")
if (-not $mutex.WaitOne(0)) {
    Add-Content $Log "$(Get-Date -Format o) another run is active; skipping"
    exit 0
}
try {
    & "$AppDir\.venv\Scripts\Activate.ps1"
    Add-Content $Log "$(Get-Date -Format o) === batch start (limit=$BatchLimit) ==="
    python run.py --once --limit $BatchLimit -v *>> $Log
    Add-Content $Log "$(Get-Date -Format o) === batch done (exit $LASTEXITCODE) ==="
}
finally {
    $mutex.ReleaseMutex()
}
