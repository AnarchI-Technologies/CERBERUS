[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $SourceRoot,

    [string] $PluginSourceRoot,

    [Parameter(Mandatory)]
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
$scaffoldRoot = $PSScriptRoot
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
if ([string]::IsNullOrWhiteSpace($PluginSourceRoot)) {
    $pluginSource = $source
}
else {
    $pluginSource = (Resolve-Path -LiteralPath $PluginSourceRoot).Path
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $output -Force | Out-Null

function Get-Relative([string] $Root, [string] $Path) {
    return [IO.Path]::GetRelativePath($Root, $Path).Replace('\', '/')
}

function Read-SourceAllowlist([string] $Path) {
    $rows = @()
    foreach ($raw in [IO.File]::ReadAllLines($Path)) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }
        $parts = $line.Split('|', 2)
        if ($parts.Count -ne 2 -or $parts[0] -notin @('required', 'optional')) {
            throw "Invalid allowlist row in $Path`: $line"
        }
        $pattern = $parts[1].Replace('\', '/')
        if ([IO.Path]::IsPathRooted($pattern) -or $pattern.Split('/') -contains '..') {
            throw "Unsafe allowlist pattern: $pattern"
        }
        $escaped = [regex]::Escape($pattern)
        $regex = '^' + $escaped.Replace('\*', '.*').Replace('\?', '.') + '$'
        $rows += [pscustomobject]@{
            Required = $parts[0] -eq 'required'
            Pattern = $pattern
            Regex = $regex
        }
    }
    return @($rows)
}

function Test-PatternMatch([string] $Relative, [object[]] $Patterns) {
    foreach ($pattern in $Patterns) {
        if ([regex]::IsMatch($Relative, $pattern.Regex)) {
            return $true
        }
    }
    return $false
}

function Copy-SourceComponent(
    [string] $ComponentSource,
    [object[]] $Patterns,
    [object[]] $ExcludedPatterns,
    [string] $DestinationRoot
) {
    $allFiles = @(
        Get-ChildItem -LiteralPath $ComponentSource -File -Recurse -Force |
            Sort-Object FullName
    )
    $matches = @{}
    foreach ($pattern in $Patterns) {
        $matches[$pattern.Pattern] = 0
    }

    foreach ($file in $allFiles) {
        $relative = Get-Relative $ComponentSource $file.FullName
        if (-not (Test-PatternMatch $relative $Patterns)) {
            continue
        }
        if ($ExcludedPatterns.Count -gt 0 -and (Test-PatternMatch $relative $ExcludedPatterns)) {
            continue
        }
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Allowlisted source cannot be a reparse point: $relative"
        }

        foreach ($pattern in $Patterns) {
            if ([regex]::IsMatch($relative, $pattern.Regex)) {
                $matches[$pattern.Pattern]++
            }
        }

        $destination = Join-Path $DestinationRoot $relative
        if (Test-Path -LiteralPath $destination) {
            throw "Duplicate component destination: $relative"
        }
        $parent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        [IO.File]::Copy($file.FullName, $destination)
    }

    foreach ($pattern in $Patterns) {
        if ($pattern.Required -and $matches[$pattern.Pattern] -eq 0) {
            throw "Required source allowlist entry did not match: $($pattern.Pattern)"
        }
    }
}

function Copy-ShellFiles([string] $DestinationRoot) {
    $allowlistPath = Join-Path $scaffoldRoot 'allowlists/shell-files.txt'
    $seen = @{}
    foreach ($raw in [IO.File]::ReadAllLines($allowlistPath)) {
        $relative = $raw.Trim().Replace('\', '/')
        if (-not $relative -or $relative.StartsWith('#')) {
            continue
        }
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Split('/') -contains '..') {
            throw "Unsafe shell allowlist path: $relative"
        }
        if ($seen.ContainsKey($relative)) {
            throw "Duplicate shell allowlist path: $relative"
        }
        $seen[$relative] = $true

        $sourcePath = Join-Path $scaffoldRoot $relative
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            throw "Shell allowlist file is missing: $relative"
        }
        $destination = Join-Path $DestinationRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        [IO.File]::Copy($sourcePath, $destination)
    }
}

function Write-Utf8Lf([string] $Path, [string] $Content) {
    $normalized = $Content.Replace("`r`n", "`n")
    [IO.File]::WriteAllText($Path, $normalized, [Text.UTF8Encoding]::new($false))
}

function New-DeterministicZip([string] $Directory, [string] $ArchivePath) {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stream = [IO.File]::Open($ArchivePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $archive = [IO.Compression.ZipArchive]::new(
            $stream,
            [IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        try {
            $fixedTime = [DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $files = @(
                Get-ChildItem -LiteralPath $Directory -File -Recurse -Force |
                    Sort-Object { Get-Relative $Directory $_.FullName } -CaseSensitive
            )
            foreach ($file in $files) {
                $relative = Get-Relative $Directory $file.FullName
                $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = $fixedTime
                if ($file.Extension -eq '.sh' -or $relative -eq 'bin/cerberus-client') {
                    $entry.ExternalAttributes = [int] 0x81ED0000
                }
                else {
                    $entry.ExternalAttributes = [int] 0x81A40000
                }
                $input = [IO.File]::OpenRead($file.FullName)
                $entryStream = $entry.Open()
                try {
                    $input.CopyTo($entryStream)
                }
                finally {
                    $entryStream.Dispose()
                    $input.Dispose()
                }
            }
        }
        finally {
            $archive.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

$stage = Join-Path $output ('.cerberus-client-build-' + [guid]::NewGuid().ToString('N'))
$completed = $false
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    Copy-ShellFiles $stage

    $corePatterns = Read-SourceAllowlist (Join-Path $scaffoldRoot 'allowlists/cerberus-core.txt')
    $pluginPatterns = Read-SourceAllowlist (Join-Path $scaffoldRoot 'allowlists/clawroyale-ai.txt')
    Copy-SourceComponent `
        -ComponentSource $source `
        -Patterns $corePatterns `
        -ExcludedPatterns $pluginPatterns `
        -DestinationRoot (Join-Path $stage 'payload/cerberus-core')
    Copy-SourceComponent `
        -ComponentSource $pluginSource `
        -Patterns $pluginPatterns `
        -ExcludedPatterns @() `
        -DestinationRoot (Join-Path $stage 'payload/plugins/clawroyale.ai')

    & (Join-Path $scaffoldRoot 'scripts/verify-bundle.ps1') -BundleRoot $stage -ScanOnly

    $contentFiles = @(
        Get-ChildItem -LiteralPath $stage -File -Recurse -Force |
            Sort-Object { Get-Relative $stage $_.FullName } -CaseSensitive
    )
    $hashRows = foreach ($file in $contentFiles) {
        $relative = Get-Relative $stage $file.FullName
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
    $hashText = ($hashRows -join "`n") + "`n"
    $hashListPath = Join-Path $stage 'files.sha256'
    Write-Utf8Lf $hashListPath $hashText

    $hashListBytes = [IO.File]::ReadAllBytes($hashListPath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $releaseId = [Convert]::ToHexString($sha.ComputeHash($hashListBytes)).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
    $manifest = [ordered]@{
        schemaVersion = 1
        releaseId = $releaseId
        contentSha256 = $releaseId
        fileCount = $contentFiles.Count
        credentialAgnostic = $true
        components = @('cerberus', 'clawroyale.ai')
    }
    $manifestText = ($manifest | ConvertTo-Json -Depth 5) + "`n"
    Write-Utf8Lf (Join-Path $stage 'bundle-manifest.json') $manifestText

    & (Join-Path $scaffoldRoot 'scripts/verify-bundle.ps1') -BundleRoot $stage

    $shortId = $releaseId.Substring(0, 16)
    $bundleName = "cerberus-client-shell-$shortId"
    $finalDirectory = Join-Path $output $bundleName
    $archivePath = Join-Path $output "$bundleName.zip"
    $archiveHashPath = "$archivePath.sha256"
    if (Test-Path -LiteralPath $finalDirectory) {
        throw "Output bundle directory already exists: $finalDirectory"
    }
    if (Test-Path -LiteralPath $archivePath) {
        throw "Output archive already exists: $archivePath"
    }

    Move-Item -LiteralPath $stage -Destination $finalDirectory
    New-DeterministicZip -Directory $finalDirectory -ArchivePath $archivePath
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Utf8Lf $archiveHashPath "$archiveHash  $bundleName.zip`n"
    $completed = $true

    Write-Host "releaseId=$releaseId"
    Write-Host "bundleDirectory=$finalDirectory"
    Write-Host "archive=$archivePath"
    Write-Host "archiveSha256=$archiveHash"
}
finally {
    if (-not $completed -and (Test-Path -LiteralPath $stage)) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
