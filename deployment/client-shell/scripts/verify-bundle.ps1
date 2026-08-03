[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $BundleRoot,

    [switch] $ScanOnly
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $BundleRoot).Path

function Get-NormalizedRelativePath([string] $Path) {
    return [IO.Path]::GetRelativePath($root, $Path).Replace('\', '/')
}

function Test-TextFile([string] $Path) {
    $extension = [IO.Path]::GetExtension($Path).ToLowerInvariant()
    return $extension -in @(
        '', '.cfg', '.conf', '.json', '.md', '.ps1', '.py', '.service',
        '.sh', '.template', '.txt', '.yaml', '.yml'
    )
}

$files = @(Get-ChildItem -LiteralPath $root -File -Recurse -Force)
$directories = @(Get-ChildItem -LiteralPath $root -Directory -Recurse -Force)

foreach ($directory in $directories) {
    $name = $directory.Name.ToLowerInvariant()
    if ($name -in @('.git', 'memory', 'memories', 'state')) {
        throw "Forbidden metadata or runtime-state directory: $(Get-NormalizedRelativePath $directory.FullName)"
    }
    if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse points are forbidden: $(Get-NormalizedRelativePath $directory.FullName)"
    }
}

$forbiddenNames = @(
    '.git', '.gitignore', '.gitattributes', '.gitmodules',
    'id_rsa', 'id_ed25519'
)
$forbiddenExtensions = @('.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db', '.db3')

$privateMarker = '-----' + 'BEGIN'
$credentialPatterns = @(
    $privateMarker,
    'BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY',
    'AKIA[0-9A-Z]{16}',
    'ASIA[0-9A-Z]{16}',
    'gh[pousr]_[A-Za-z0-9]{20,}',
    'xox[baprs]-[A-Za-z0-9-]{10,}',
    'sk-(?:proj-)?[A-Za-z0-9_-]{16,}',
    '(?m)^[^\S\r\n]*(?:[A-Z][A-Z0-9_]*(?:API_KEY|PRIVATE_KEY|PASSWORD|SECRET|TOKEN)|SECRET|TOKEN|PASSWORD)[^\S\r\n]*=[^\S\r\n]*["'']?(?!(?:re\.compile|hashlib\.|os\.getenv)\b)[^#\s"'']{6,}',
    '(?m)^[^\S\r\n]*MONGODB_URI[^\S\r\n]*=[^\S\r\n]*["'']?[^#\s"'']{6,}',
    '(?i)["''](?:api[_-]?key|private[_-]?key|password|secret|token)["'']\s*:\s*["''][^"'']{8,}["'']'
)
$machinePathPatterns = @(
    '(?i)[A-Za-z]:\\Users\\[^\\\s]+',
    '(?i)/mnt/[a-z]/Users/[^/\s]+/',
    '(?i)/home/[A-Za-z0-9._-]+/'
)

foreach ($file in $files) {
    $relative = Get-NormalizedRelativePath $file.FullName
    $name = $file.Name.ToLowerInvariant()
    $extension = $file.Extension.ToLowerInvariant()

    if ($name -eq '.env' -or $name.StartsWith('.env.')) {
        throw "Environment files are forbidden: $relative"
    }
    if ($name -in $forbiddenNames -or $extension -in $forbiddenExtensions) {
        throw "Forbidden credential, state, or metadata file: $relative"
    }
    if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse points are forbidden: $relative"
    }
    if (-not (Test-TextFile $file.FullName)) {
        continue
    }

    $content = [IO.File]::ReadAllText($file.FullName)
    foreach ($pattern in $credentialPatterns) {
        if ([regex]::IsMatch($content, $pattern)) {
            throw "Credential-like value or private-key material found: $relative"
        }
    }
    foreach ($pattern in $machinePathPatterns) {
        if ([regex]::IsMatch($content, $pattern)) {
            throw "Machine-specific user path found: $relative"
        }
    }
}

if (-not $ScanOnly) {
    $hashListPath = Join-Path $root 'files.sha256'
    $manifestPath = Join-Path $root 'bundle-manifest.json'
    if (-not (Test-Path -LiteralPath $hashListPath -PathType Leaf)) {
        throw 'files.sha256 is missing.'
    }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'bundle-manifest.json is missing.'
    }

    $expected = @{}
    foreach ($line in [IO.File]::ReadAllLines($hashListPath)) {
        if ($line -notmatch '^([0-9a-f]{64})  ([^\r\n]+)$') {
            throw "Invalid files.sha256 row: $line"
        }
        if ($expected.ContainsKey($Matches[2])) {
            throw "Duplicate hash-list path: $($Matches[2])"
        }
        $expected[$Matches[2]] = $Matches[1]
    }

    $actualFiles = @(
        $files |
            ForEach-Object { Get-NormalizedRelativePath $_.FullName } |
            Where-Object { $_ -notin @('bundle-manifest.json', 'files.sha256') } |
            Sort-Object -CaseSensitive
    )
    $expectedFiles = @($expected.Keys | Sort-Object -CaseSensitive)
    if (($actualFiles -join "`n") -cne ($expectedFiles -join "`n")) {
        throw 'Hash-list paths do not exactly match bundle files.'
    }

    foreach ($relative in $actualFiles) {
        $actualHash = (Get-FileHash -LiteralPath (Join-Path $root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -cne $expected[$relative]) {
            throw "Hash mismatch: $relative"
        }
    }

    $hashListBytes = [IO.File]::ReadAllBytes($hashListPath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $releaseId = [Convert]::ToHexString($sha.ComputeHash($hashListBytes)).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.releaseId -cne $releaseId -or $manifest.contentSha256 -cne $releaseId) {
        throw 'Bundle manifest content hash is invalid.'
    }
    if ([int] $manifest.fileCount -ne $actualFiles.Count) {
        throw 'Bundle manifest file count is invalid.'
    }
}

Write-Host 'Bundle verification passed.'
