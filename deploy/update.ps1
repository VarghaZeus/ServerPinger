<#
.SYNOPSIS
    Update ServerPinger in place on Windows: pull, reinstall, migrate, restart.

.DESCRIPTION
    Deliberately manual - there is no self-update button in the web UI.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\deploy\update.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "ServerPinger"
)

$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Venv = Join-Path $AppDir ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

Set-Location $AppDir

$before = "unknown"
if (Test-Path (Join-Path $AppDir "VERSION")) {
    $before = (Get-Content (Join-Path $AppDir "VERSION") -TotalCount 1).Trim()
}
Write-Output "==> Current version: $before"

Write-Output "==> git pull"
& git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed." }

if (-not (Test-Path $VenvPython)) {
    throw "No virtualenv at $Venv. Run deploy\install.ps1 first."
}

Write-Output "==> Installing requirements"
& $VenvPython -m pip install -r (Join-Path $AppDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

Write-Output "==> Applying database migrations"
& $VenvPython (Join-Path $AppDir "run.py") --init-db
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }

$after = "unknown"
if (Test-Path (Join-Path $AppDir "VERSION")) {
    $after = (Get-Content (Join-Path $AppDir "VERSION") -TotalCount 1).Trim()
}
Write-Output "==> New version: $after"

# Restart whichever registration method is in use.
$restarted = $false

$nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $service) {
    Write-Output "==> Restarting the $ServiceName service"
    if ($null -ne $nssm) {
        & $nssm.Source restart $ServiceName
    } else {
        Restart-Service -Name $ServiceName -Force
    }
    $restarted = $true
}

if (-not $restarted) {
    $task = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Write-Output "==> Restarting the $ServiceName scheduled task"
        Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $ServiceName
        $restarted = $true
    }
}

if (-not $restarted) {
    Write-Output "==> No service or task named $ServiceName found; restart ServerPinger yourself."
}
