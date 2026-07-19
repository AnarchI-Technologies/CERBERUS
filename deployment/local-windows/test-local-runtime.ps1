[CmdletBinding()]
param(
    [int]$Port = 10000,
    [int]$TimeoutSeconds = 10
)

$ErrorActionPreference = 'Stop'
$uri = "http://127.0.0.1:$Port/healthz"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    try {
        $response = Invoke-RestMethod -Uri $uri -TimeoutSec 2
        [pscustomobject]@{
            Reachable = $true
            Ready = [bool]$response.ok
            Service = $response.service
            MemoryDir = $response.memory_dir
            Url = $uri
        }
        exit 0
    } catch {
        Start-Sleep -Milliseconds 500
    }
} while ((Get-Date) -lt $deadline)

throw "CERBERUS local runtime did not answer within $TimeoutSeconds seconds: $uri"

