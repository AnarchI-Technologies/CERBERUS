[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [int]$Port = 10000
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "CERBERUS virtual environment is missing: $python"
}

$dataRoot = if ($env:CERBERUS_LOCAL_DATA_DIR) {
    $env:CERBERUS_LOCAL_DATA_DIR
} else {
    Join-Path $env:LOCALAPPDATA 'CERBERUS'
}
$memoryRoot = Join-Path $dataRoot 'memory'
$logRoot = Join-Path $dataRoot 'logs'
New-Item -ItemType Directory -Path $memoryRoot, $logRoot -Force | Out-Null

$env:PORT = [string]$Port
$env:CERBERUS_BIND_HOST = '127.0.0.1'
$env:CERBERUS_MEMORY_DIR = $memoryRoot
if (-not $env:CERBERUS_MEMORY_BACKEND) { $env:CERBERUS_MEMORY_BACKEND = 'sqlite' }
if (-not $env:CERBERUS_MODEL_GATEWAY_ENABLED) { $env:CERBERUS_MODEL_GATEWAY_ENABLED = 'false' }

$log = Join-Path $logRoot 'runtime.log'
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 10MB) {
    $archive = Join-Path $logRoot ('runtime-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Move-Item -LiteralPath $log -Destination $archive
}

Set-Location -LiteralPath $RepoRoot
"[$(Get-Date -Format o)] starting deterministic local CERBERUS runtime" | Add-Content -LiteralPath $log
& $python (Join-Path $RepoRoot 'src\render_app.py') *>> $log
exit $LASTEXITCODE

