[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$TaskName = 'CERBERUS-Local-Runtime',
    [int]$Port = 10000,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $bootstrap = Get-Command python -ErrorAction SilentlyContinue
    if (-not $bootstrap) {
        $bootstrap = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $bootstrap) {
        throw 'Python 3.12+ is required to create the local CERBERUS environment.'
    }
    & $bootstrap.Source -m venv (Join-Path $RepoRoot '.venv')
}

& $venvPython -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $RepoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'CERBERUS dependency installation failed.' }

$launcher = Join-Path $PSScriptRoot 'start-cerberus.ps1'
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$launcher`" -RepoRoot `"$RepoRoot`" -Port $Port"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = 'PT10S'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Local deterministic CERBERUS runtime; no Render, Railway, or Ollama dependency.' `
    -Force | Out-Null

if ($Start) { Start-ScheduledTask -TaskName $TaskName }

[pscustomobject]@{
    Installed = $true
    TaskName = $TaskName
    RepoRoot = $RepoRoot
    HealthUrl = "http://127.0.0.1:$Port/healthz"
    DataRoot = (Join-Path $env:LOCALAPPDATA 'CERBERUS')
    Started = [bool]$Start
}
