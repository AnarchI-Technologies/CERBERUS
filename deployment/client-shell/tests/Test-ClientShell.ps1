[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Assert-True([bool] $Condition, [string] $Message) {
    if (-not $Condition) {
        throw $Message
    }
}

$manifest = Get-Content -LiteralPath (Join-Path $root 'product-manifest.json') -Raw | ConvertFrom-Json
Assert-True `
    ($manifest.anarCoreKernelInvariant.statement -eq 'an account may access only data scoped to that account') `
    'Anar Core account-data invariant is missing.'
Assert-True `
    ($manifest.desktopControlContract.webConsoleIncluded -eq $false) `
    'The client shell must not bundle a web console.'

$tenantUnit = Get-Content -LiteralPath (Join-Path $root 'deploy/systemd/cerberus-core@.service') -Raw
Assert-True ($tenantUnit.Contains('User=cerberus-%i')) 'Managed core lacks a tenant OS identity.'
Assert-True ($tenantUnit.Contains('secure_core_launcher.py')) 'Managed core lacks the default-deny route launcher.'
Assert-True ($tenantUnit.Contains('core.env')) 'Managed core lacks its protected loopback binding file.'
Assert-True (-not $tenantUnit.Contains('PrivateNetwork=true')) 'Managed core must use the stable host network namespace.'

$gatewayUnit = Get-Content -LiteralPath (Join-Path $root 'deploy/systemd/cerberus-gateway.service.template') -Raw
Assert-True ($gatewayUnit.Contains('PartOf={{CORE_SERVICE}}')) 'Explicit core restarts must propagate to the gateway.'
Assert-True (-not $gatewayUnit.Contains('JoinsNamespaceOf=')) 'Gateway must not join a process-owned network namespace.'

$gateway = Get-Content -LiteralPath (Join-Path $root 'runtime/auth_gateway.py') -Raw
Assert-True ($gateway.Contains('HEALTH_PATHS')) 'Default-deny gateway is missing.'
Assert-True ($gateway.Contains('unauthorized')) 'Gateway authentication is missing.'

$caddy = Get-Content -LiteralPath (Join-Path $root 'deploy/managed/Caddyfile.tenant.template')
$proxyRows = @($caddy | Where-Object { $_ -match '^\s*reverse_proxy\s+' })
Assert-True ($proxyRows.Count -eq 1) 'Caddy tenant template must contain exactly one upstream.'
Assert-True `
    ($proxyRows[0].Trim() -ceq 'reverse_proxy 127.0.0.1:{{LOOPBACK_PORT}}') `
    'Caddy tenant route must point only to the tenant loopback marker.'

$validator = Get-Content -LiteralPath (Join-Path $root 'runtime/config_validator.py') -Raw
Assert-True ($validator.Contains('ACCOUNT_RUNTIME_KEYS')) 'Strict account runtime allowlist is missing.'
Assert-True ($validator.Contains('AGENT_CREDENTIAL_KEYS')) 'Strict agent credential allowlist is missing.'
Assert-True ($validator.Contains('MONGO_BACKENDS')) 'Mongo backend alias isolation is missing.'

$common = Get-Content -LiteralPath (Join-Path $root 'lib/common.sh') -Raw
Assert-True ($common.Contains('bundle file set mismatch')) 'Linux verifier lacks exact-set enforcement.'
Assert-True ($common.Contains('install_systemd_unit')) 'Systemd unit backup/install helper is missing.'

$installer = Get-Content -LiteralPath (Join-Path $root 'scripts/install.sh') -Raw
Assert-True ($installer.Contains('PYTHONPYCACHEPREFIX=')) 'Compile checks could mutate the immutable release.'
Assert-True ($installer.Contains('core_unit="cerberus-core@.service"')) 'Managed install is not unit-scoped.'
Assert-True ($installer.Contains('core_unit="cerberus-core.service"')) 'Self-hosted install is not unit-scoped.'

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    throw 'Python is required to run the two-account gateway tests.'
}

if ($python.Name -eq 'py.exe') {
    & $python.Source -3 -m unittest discover -s (Join-Path $root 'tests') -p 'test_*.py' -v
}
else {
    & $python.Source -m unittest discover -s (Join-Path $root 'tests') -p 'test_*.py' -v
}
if ($LASTEXITCODE -ne 0) {
    throw 'Client shell Python acceptance tests failed.'
}

Write-Host 'Client shell isolation tests passed.'
