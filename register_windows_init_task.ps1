param(
    [string]$TaskName = "GSE2_WindowsInit",
    [string]$BatPath = (Join-Path $PSScriptRoot "windows_init.bat")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Path -LiteralPath $BatPath)) {
    throw "Batch file not found: $BatPath"
}

if (-not (Test-IsAdmin)) {
    throw "Run this script in an elevated PowerShell session (Run as Administrator)."
}

$escapedBatPath = $BatPath.Replace('"', '""')
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$escapedBatPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Run windows_init.bat at system startup." `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' created/updated."
Write-Host "Task runs at startup as SYSTEM:"
Write-Host "  $BatPath"
