[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $CheckOnly,
    [switch] $DryRun,
    [switch] $Apply,
    [switch] $RestartWsl,
    [switch] $SkipRuntimeInstall
)

$ErrorActionPreference = 'Stop'
$bundleRoot = $PSScriptRoot

if ($Apply -and ($CheckOnly -or $DryRun)) {
    throw '-Apply cannot be combined with -CheckOnly or -DryRun.'
}
$mutate = $Apply.IsPresent

$readiness = [ordered]@{
    schemaVersion = 1
    mode = if ($mutate) { 'apply' } else { 'check-only' }
    distro = $Distro
    windowsFeaturesReady = $false
    distroReady = $false
    systemdReady = $false
    bundleVerified = $false
    isolationTestsPassed = $false
    runtimeInstalled = $false
    rebootRequired = $false
    wslRestartRequired = $false
    ready = $false
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Distros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return @()
    }
    $raw = (& wsl.exe --list --quiet 2>$null) -join "`n"
    return @(
        $raw.Replace([char] 0, '').Split("`n") |
            ForEach-Object Trim |
            Where-Object { $_ }
    )
}

function Convert-ToWslPath([string] $WindowsPath) {
    $resolved = (Resolve-Path -LiteralPath $WindowsPath).Path
    $converted = & wsl.exe -d $Distro -- wslpath -a -- $resolved
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Could not map into WSL: $WindowsPath"
    }
    return $converted.Trim()
}

try {
    & (Join-Path $bundleRoot 'scripts/verify-bundle.ps1') -BundleRoot $bundleRoot
    $readiness.bundleVerified = $true

    & (Join-Path $bundleRoot 'tests/Test-ClientShell.ps1')
    $readiness.isolationTestsPassed = $true

    $features = @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')
    $featureStates = @{}
    foreach ($feature in $features) {
        try {
            $state = Get-WindowsOptionalFeature -Online -FeatureName $feature
            $featureStates[$feature] = [string] $state.State
        }
        catch {
            $featureStates[$feature] = 'Unknown'
        }
    }
    $missingFeatures = @($features | Where-Object { $featureStates[$_] -ne 'Enabled' })

    if ($missingFeatures.Count -gt 0 -and $mutate) {
        if (-not (Test-Administrator)) {
            throw 'Run PowerShell as Administrator to enable WSL2 prerequisites.'
        }
        foreach ($feature in $missingFeatures) {
            Write-Host "Enabling Windows feature: $feature"
            $result = Enable-WindowsOptionalFeature `
                -Online `
                -FeatureName $feature `
                -All `
                -NoRestart
            if ($result.RestartNeeded) {
                $readiness.rebootRequired = $true
            }
        }
    }
    elseif ($missingFeatures.Count -gt 0) {
        Write-Host "Check only: Windows features still needed: $($missingFeatures -join ', ')"
    }
    $readiness.windowsFeaturesReady = $missingFeatures.Count -eq 0

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        if ($mutate) {
            throw 'wsl.exe is unavailable after feature provisioning; reboot Windows and rerun.'
        }
        Write-Host 'Check only: wsl.exe is not available.'
    }
    else {
        $distros = Get-Distros
        if ($Distro -notin $distros -and $mutate -and -not $readiness.rebootRequired) {
            Write-Host "Installing WSL distribution: $Distro"
            & wsl.exe --install --distribution $Distro --no-launch
            if ($LASTEXITCODE -ne 0) {
                throw "WSL could not install '$Distro'."
            }
            $distros = Get-Distros
        }
        elseif ($Distro -notin $distros) {
            Write-Host "Check only: WSL distribution '$Distro' is not installed."
        }
        $readiness.distroReady = $Distro -in $distros
    }

    if ($readiness.distroReady) {
        & wsl.exe -d $Distro -u root -- sh -lc 'test "$(ps -p 1 -o comm=)" = systemd'
        $readiness.systemdReady = $LASTEXITCODE -eq 0

        if (-not $readiness.systemdReady -and $mutate) {
            $wslRoot = Convert-ToWslPath $bundleRoot
            & wsl.exe -d $Distro -u root -- bash "$wslRoot/scripts/enable-wsl-systemd.sh"
            if ($LASTEXITCODE -ne 0) {
                throw 'Could not enable systemd in WSL.'
            }
            $readiness.wslRestartRequired = $true
            if ($RestartWsl) {
                Write-Host 'Restarting WSL because -RestartWsl was explicitly selected.'
                & wsl.exe --shutdown
                Start-Sleep -Seconds 2
                & wsl.exe -d $Distro -u root -- sh -lc 'test "$(ps -p 1 -o comm=)" = systemd'
                $readiness.systemdReady = $LASTEXITCODE -eq 0
                $readiness.wslRestartRequired = -not $readiness.systemdReady
            }
        }
    }

    if ($mutate -and
        -not $SkipRuntimeInstall -and
        $readiness.distroReady -and
        $readiness.systemdReady -and
        -not $readiness.rebootRequired) {
        $wslRoot = Convert-ToWslPath $bundleRoot
        & wsl.exe -d $Distro -u root -- bash "$wslRoot/bin/cerberus-client" install
        if ($LASTEXITCODE -ne 0) {
            throw 'CERBERUS runtime installation failed.'
        }
        & wsl.exe -d $Distro -u root -- bash "$wslRoot/bin/cerberus-client" doctor
        if ($LASTEXITCODE -ne 0) {
            throw 'CERBERUS doctor found a blocking issue.'
        }
        $readiness.runtimeInstalled = $true
    }

    $readiness.ready = (
        $readiness.windowsFeaturesReady -and
        $readiness.distroReady -and
        $readiness.systemdReady -and
        $readiness.bundleVerified -and
        $readiness.isolationTestsPassed -and
        -not $readiness.rebootRequired -and
        -not $readiness.wslRestartRequired
    )
}
finally {
    Write-Output ('READINESS_JSON=' + ($readiness | ConvertTo-Json -Compress))
}

if ($readiness.rebootRequired -or $readiness.wslRestartRequired) {
    exit 10
}
if (-not $readiness.ready) {
    exit 1
}
