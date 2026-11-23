param(
    [switch]$Once,
    [int]$IntervalSeconds = 15
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Sync-Repo {
    $changes = git status --porcelain
    if (-not $changes) {
        return
    }
    Start-Sleep -Seconds 3
    git add -A
    $changes = git status --porcelain
    if (-not $changes) {
        return
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git commit -m "Auto-update $stamp"
    # post-commit hook pushes to origin/main; fallback push if hook didn't run
    $ahead = git rev-list --count origin/main..main 2>$null
    if ($ahead -and [int]$ahead -gt 0) {
        git push origin main
    }
    Write-Host "[$stamp] Synced to GitHub."
}

if ($Once) {
    Sync-Repo
    exit 0
}

Write-Host "ClipMic auto-sync running (Ctrl+C to stop)..."
while ($true) {
    try {
        Sync-Repo
    } catch {
        Write-Warning "Sync error: $_"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
