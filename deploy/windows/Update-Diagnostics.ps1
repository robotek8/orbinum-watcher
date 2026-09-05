[CmdletBinding()]
param(
    [string]$Root = "C:\OrbinumWatcher",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$repo = "https://raw.githubusercontent.com/robotek8/orbinum-watcher/$Branch"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$temp = Join-Path $env:TEMP "orbinum-diagnostics-$stamp"
New-Item -ItemType Directory -Path $temp -Force | Out-Null

$files = @(
    @{ Remote = "analysis/anomaly_detector.py"; Local = "analysis\anomaly_detector.py" },
    @{ Remote = "analysis/stress_event_report.py"; Local = "analysis\stress_event_report.py" },
    @{ Remote = "agent/windows_event_sync.py"; Local = "windows_event_sync.py" }
)

try {
    foreach ($file in $files) {
        $target = Join-Path $temp ([IO.Path]::GetFileName($file.Remote))
        Invoke-WebRequest -UseBasicParsing -Uri "$repo/$($file.Remote)" -OutFile $target
    }

    $python = (Get-Command py.exe -ErrorAction Stop).Source
    foreach ($file in $files) {
        $target = Join-Path $temp ([IO.Path]::GetFileName($file.Remote))
        & $python -3 -m py_compile $target
        if ($LASTEXITCODE -ne 0) {
            throw "py_compile failed for $($file.Remote)"
        }
    }

    foreach ($file in $files) {
        $source = Join-Path $temp ([IO.Path]::GetFileName($file.Remote))
        $destination = Join-Path $Root $file.Local
        $directory = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        if (Test-Path $destination) {
            Copy-Item $destination "$destination.bak-$stamp" -Force
        }
        Copy-Item $source $destination -Force
        Write-Host "Updated $destination"
    }

    $taskName = "Orbinum Event Sync"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 2
        $task = Get-ScheduledTask -TaskName $taskName
        Write-Host "Task $taskName: $($task.State)"
    }
    else {
        Write-Warning "Scheduled task '$taskName' was not found; files were updated but the running sync process was not restarted."
    }

    Write-Host "Diagnostics update complete. Validator, Docker and SSH tunnels were not controlled by this script."
}
finally {
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
}
