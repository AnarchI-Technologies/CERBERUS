[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'doctor', 'install', 'configure', 'deploy', 'status', 'prepare-render')]
    [string] $Command = 'help',

    [ValidateSet('self-hosted', 'managed')]
    [string] $Scope = 'self-hosted',

    [string] $Tenant,
    [string[]] $Agent,
    [int] $Port = 10000,
    [string] $Release,
    [string] $RuntimeFile,
    [string] $CredentialsFile,
    [string] $AgentRuntimeFile,
    [string] $AgentCredentialsFile,
    [string] $Destination,
    [string] $Distro = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
$bundleRoot = $PSScriptRoot

function Show-Help {
    @'
CERBERUS + clawroyale.ai client shell

  .\cerberus-client.ps1 doctor
  .\cerberus-client.ps1 install [-Agent agent-one,agent-two]
  .\cerberus-client.ps1 configure -RuntimeFile FILE -CredentialsFile FILE
  .\cerberus-client.ps1 configure -Agent agent-one `
      -AgentRuntimeFile FILE -AgentCredentialsFile FILE
  .\cerberus-client.ps1 deploy [-Agent agent-one,agent-two]
  .\cerberus-client.ps1 status [-Agent agent-one,agent-two]
  .\cerberus-client.ps1 prepare-render -Destination DIRECTORY

Add -Scope managed -Tenant customer-slug for managed-account operations.
Status emits stable key=value rows for a future standalone Windows client.
'@
}

function Assert-Wsl {
    $wsl = Get-Command 'wsl.exe' -ErrorAction SilentlyContinue
    if (-not $wsl) {
        throw 'WSL2 is not installed. Install WSL2 and Ubuntu, then try again.'
    }

    & wsl.exe -d $Distro -- true
    if ($LASTEXITCODE -ne 0) {
        throw "The WSL distribution '$Distro' is not available."
    }
}

function Convert-ToWslPath([string] $WindowsPath) {
    $resolved = (Resolve-Path -LiteralPath $WindowsPath).Path
    $converted = & wsl.exe -d $Distro -- wslpath -a -- $resolved
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($converted)) {
        throw "Could not map this file into WSL: $WindowsPath"
    }
    return $converted.Trim()
}

function Invoke-WslClient([string[]] $ClientArguments) {
    Assert-Wsl
    $wslRoot = Convert-ToWslPath $bundleRoot
    & wsl.exe -d $Distro -- sudo bash "$wslRoot/bin/cerberus-client" @ClientArguments
    if ($LASTEXITCODE -ne 0) {
        throw "CERBERUS command failed with exit code $LASTEXITCODE."
    }
}

if ($Command -eq 'help') {
    Show-Help
    exit 0
}

if ($Command -eq 'prepare-render') {
    if ([string]::IsNullOrWhiteSpace($Destination)) {
        throw '-Destination is required for prepare-render.'
    }
    & "$bundleRoot/scripts/prepare-render.ps1" -BundleRoot $bundleRoot -Destination $Destination
    exit $LASTEXITCODE
}

$arguments = @($Command, '--scope', $Scope)
if (-not [string]::IsNullOrWhiteSpace($Tenant)) {
    $arguments += @('--tenant', $Tenant)
}
foreach ($agentName in $Agent) {
    if (-not [string]::IsNullOrWhiteSpace($agentName)) {
        $arguments += @('--agent', $agentName)
    }
}

switch ($Command) {
    'install' {
        $arguments += @('--port', [string] $Port)
    }
    'configure' {
        Assert-Wsl
        if ($Agent.Count -gt 1) {
            throw 'Configure one agent at a time.'
        }
        if ($Agent.Count -eq 1) {
            if ([string]::IsNullOrWhiteSpace($AgentRuntimeFile) -or
                [string]::IsNullOrWhiteSpace($AgentCredentialsFile)) {
                throw '-AgentRuntimeFile and -AgentCredentialsFile are required.'
            }
            $arguments += @(
                '--agent-runtime', (Convert-ToWslPath $AgentRuntimeFile),
                '--agent-credentials', (Convert-ToWslPath $AgentCredentialsFile)
            )
        }
        else {
            if ([string]::IsNullOrWhiteSpace($RuntimeFile) -or
                [string]::IsNullOrWhiteSpace($CredentialsFile)) {
                throw '-RuntimeFile and -CredentialsFile are required.'
            }
            $arguments += @(
                '--runtime', (Convert-ToWslPath $RuntimeFile),
                '--credentials', (Convert-ToWslPath $CredentialsFile)
            )
        }
    }
    'deploy' {
        if (-not [string]::IsNullOrWhiteSpace($Release)) {
            $arguments += @('--release', $Release)
        }
    }
}

Invoke-WslClient $arguments
