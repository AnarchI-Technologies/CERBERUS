[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('cerberus-client-builder-' + [guid]::NewGuid().ToString('N'))

function Assert-True([bool] $Condition, [string] $Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Write-Text([string] $Path, [string] $Text) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

try {
    $source = Join-Path $testRoot 'source'
    $outA = Join-Path $testRoot 'out-a'
    $outB = Join-Path $testRoot 'out-b'
    $outMissingWorker = Join-Path $testRoot 'out-missing-worker'
    $outSecret = Join-Path $testRoot 'out-secret'

    Write-Text (Join-Path $source 'requirements.txt') "requests>=2`n"
    Write-Text (Join-Path $source 'src/render_app.py') "print('core')`n"
    Write-Text (Join-Path $source 'src/decision_bridge.py') "VALUE = 'bridge'`n"
    Write-Text (Join-Path $source 'src/core_loop.py') "VALUE = 1`n"
    Write-Text (Join-Path $source 'src/clawroyale_ai/__init__.py') "__all__ = []`n"
    Write-Text (Join-Path $source 'src/clawroyale_ai/adapter.py') "VALUE = 'adapter'`n"
    Write-Text (Join-Path $source 'src/clawroyale_ai/catalog.py') "VALUE = 'catalog'`n"
    Write-Text (Join-Path $source 'src/clawroyale_ai/contracts.py') "VALUE = 'contracts'`n"
    Write-Text (Join-Path $source 'src/clawroyale_ai/strategies.py') "VALUE = 'strategies'`n"
    Write-Text (Join-Path $source '.env') "SHOULD_NOT_SHIP=yes`n"
    Write-Text (Join-Path $source '.git/config') "[core]`n"
    Write-Text (Join-Path $source 'state.sqlite') "not a real database`n"
    Write-Text (Join-Path $source 'unlisted.txt') "not allowlisted`n"

    $missingWorkerRejected = $false
    try {
        & (Join-Path $root 'build-client-bundle.ps1') `
            -SourceRoot $source `
            -OutputDirectory $outMissingWorker
    }
    catch {
        $missingWorkerRejected = $true
    }
    Assert-True $missingWorkerRejected 'Builder accepted a bundle without the required plugin worker.'
    Write-Text (Join-Path $source 'src/claw_runtime.py') "print('worker')`n"

    & (Join-Path $root 'build-client-bundle.ps1') `
        -SourceRoot $source `
        -OutputDirectory $outA
    & (Join-Path $root 'build-client-bundle.ps1') `
        -SourceRoot $source `
        -OutputDirectory $outB

    $zipA = @(Get-ChildItem -LiteralPath $outA -Filter '*.zip' -File)
    $zipB = @(Get-ChildItem -LiteralPath $outB -Filter '*.zip' -File)
    Assert-True ($zipA.Count -eq 1 -and $zipB.Count -eq 1) 'Builder did not create one archive per run.'
    $hashA = (Get-FileHash -LiteralPath $zipA[0].FullName -Algorithm SHA256).Hash
    $hashB = (Get-FileHash -LiteralPath $zipB[0].FullName -Algorithm SHA256).Hash
    Assert-True ($hashA -ceq $hashB) 'Identical inputs did not create a deterministic archive.'

    $bundleA = @(Get-ChildItem -LiteralPath $outA -Directory)[0]
    $renderDestination = Join-Path $testRoot 'render-source'
    & (Join-Path $bundleA.FullName 'scripts/prepare-render.ps1') `
        -BundleRoot $bundleA.FullName `
        -Destination $renderDestination
    Assert-True (Test-Path -LiteralPath (Join-Path $renderDestination 'render.yaml')) 'Render template was not prepared.'
    Assert-True (Test-Path -LiteralPath (Join-Path $renderDestination 'src/render_app.py')) 'Render core payload was not overlaid.'
    Assert-True (Test-Path -LiteralPath (Join-Path $renderDestination 'src/clawroyale_ai/adapter.py')) 'Render plugin payload was not overlaid.'

    $extraPath = Join-Path $bundleA.FullName 'unlisted-extra.py'
    Write-Text $extraPath "raise SystemExit('must not be trusted')`n"
    $extraRejected = $false
    try {
        & (Join-Path $bundleA.FullName 'scripts/verify-bundle.ps1') -BundleRoot $bundleA.FullName
    }
    catch {
        $extraRejected = $true
    }
    Assert-True $extraRejected 'Exact-set verification accepted an unlisted executable Python file.'
    Remove-Item -LiteralPath $extraPath -Force

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipA[0].FullName)
    try {
        $entries = @($archive.Entries | ForEach-Object FullName)
        Assert-True (-not ($entries -match '(^|/)\.env($|\.)')) '.env entered the archive.'
        Assert-True (-not ($entries -match '(^|/)\.git($|/)')) 'Git metadata entered the archive.'
        Assert-True (-not ($entries -contains 'payload/cerberus-core/state.sqlite')) 'SQLite state entered the archive.'
        Assert-True (-not ($entries -contains 'payload/cerberus-core/unlisted.txt')) 'Unallowlisted file entered the archive.'
    }
    finally {
        $archive.Dispose()
    }

    Write-Text (Join-Path $source 'src/render_app.py') "CERBERUS_HTTP_TOKEN=definitely-not-allowed`n"
    $secretRejected = $false
    try {
        & (Join-Path $root 'build-client-bundle.ps1') `
            -SourceRoot $source `
            -OutputDirectory $outSecret
    }
    catch {
        $secretRejected = $true
    }
    Assert-True $secretRejected 'Builder accepted a non-empty credential assignment.'

    $scanRoot = Join-Path $testRoot 'scan-state'
    Write-Text (Join-Path $scanRoot 'memory.db') "state`n"
    $stateRejected = $false
    try {
        & (Join-Path $root 'scripts/verify-bundle.ps1') -BundleRoot $scanRoot -ScanOnly
    }
    catch {
        $stateRejected = $true
    }
    Assert-True $stateRejected 'Security scan accepted a SQLite/memory artifact.'

    Write-Host 'Bundle builder tests passed.'
}
finally {
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $resolvedTest = [IO.Path]::GetFullPath($testRoot)
    if ($resolvedTest.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTest).StartsWith('cerberus-client-builder-')) {
        Remove-Item -LiteralPath $resolvedTest -Recurse -Force -ErrorAction SilentlyContinue
    }
}
