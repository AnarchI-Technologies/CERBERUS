[CmdletBinding()]
param([string]$TaskName = 'CERBERUS-Local-Runtime')

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

[pscustomobject]@{
    Installed = $false
    TaskName = $TaskName
    DataPreserved = (Join-Path $env:LOCALAPPDATA 'CERBERUS')
}

