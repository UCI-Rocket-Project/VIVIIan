param(
    [string]$TaskName = "GSE2_WindowsInit"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    throw "Run this script in an elevated PowerShell session (Run as Administrator)."
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Scheduled task '$TaskName' does not exist."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Scheduled task '$TaskName' removed."
