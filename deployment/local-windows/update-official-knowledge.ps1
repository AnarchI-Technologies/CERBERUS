[CmdletBinding()]
param(
    [string]$WslDistribution = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "CERBERUS Python environment not found: $python"
}

$wslRoot = "\\wsl.localhost\$WslDistribution\var\data\.cerberus"
if (-not (Test-Path -LiteralPath $wslRoot)) {
    throw "CERBERUS WSL runtime storage is unavailable: $wslRoot"
}

$snapshot = Join-Path $wslRoot "claw_royale_canonical_snapshot.md"
$index = Join-Path $wslRoot "official_knowledge_index.json"

& $python (Join-Path $repo "src\claw_knowledge_sync.py") --output $snapshot
if ($LASTEXITCODE -ne 0) {
    throw "Official Claw knowledge sync failed with exit code $LASTEXITCODE"
}

$oldGateway = $env:CERBERUS_MODEL_GATEWAY_ENABLED
try {
    $env:CERBERUS_MODEL_GATEWAY_ENABLED = "true"
    & $python (Join-Path $repo "src\knowledge_retrieval.py") --output $index
    if ($LASTEXITCODE -ne 0) {
        throw "Local knowledge index failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $oldGateway) {
        Remove-Item Env:CERBERUS_MODEL_GATEWAY_ENABLED -ErrorAction SilentlyContinue
    }
    else {
        $env:CERBERUS_MODEL_GATEWAY_ENABLED = $oldGateway
    }
}

Write-Output "Official knowledge and local retrieval index are current."
