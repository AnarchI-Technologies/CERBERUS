[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BundleRoot,

    [Parameter(Mandatory)]
    [string] $Destination
)

$ErrorActionPreference = 'Stop'
$resolvedBundle = (Resolve-Path -LiteralPath $BundleRoot).Path
$fullDestination = [IO.Path]::GetFullPath($Destination)

if (Test-Path -LiteralPath $fullDestination) {
    throw 'Destination already exists; choose a new path.'
}

$completed = $false
try {
    New-Item -ItemType Directory -Path $fullDestination | Out-Null
    Copy-Item -Path (Join-Path $resolvedBundle 'payload/cerberus-core/*') `
        -Destination $fullDestination -Recurse -Force
    Copy-Item -Path (Join-Path $resolvedBundle 'payload/plugins/clawroyale.ai/*') `
        -Destination $fullDestination -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $resolvedBundle 'runtime') `
        -Destination (Join-Path $fullDestination 'runtime') -Recurse
    Copy-Item -LiteralPath (Join-Path $resolvedBundle 'deploy/render/render.yaml.template') `
        -Destination (Join-Path $fullDestination 'render.yaml')

    & (Join-Path $resolvedBundle 'scripts/verify-bundle.ps1') `
        -BundleRoot $fullDestination `
        -ScanOnly
    if ($LASTEXITCODE -ne 0) {
        throw 'Prepared Render source failed the security scan.'
    }
    $completed = $true
}
finally {
    if (-not $completed -and (Test-Path -LiteralPath $fullDestination)) {
        Remove-Item -LiteralPath $fullDestination -Recurse -Force
    }
}

Write-Host 'Clean Render source prepared:'
Write-Host $fullDestination
Write-Host
Write-Host 'Enter every sync:false value in your own Render dashboard.'
Write-Host 'Do not add a credential file to this folder.'
