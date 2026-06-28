#requires -Version 5.1
<#
  Registers a Windows Scheduled Task "OpenEye Marketplace Scan" that runs scripts/scan.ps1
  three times daily (8am / 12pm / 6pm). Re-run to update (-Force replaces).

  The task runs as the current user, only when logged on - required, because the Facebook
  MCP relies on your interactive desktop browser session.

  -WithMcpAutostart also registers "OpenEye FB MCP" to launch the MCP server at logon.
  (You must still complete the Facebook login manually once; see scripts/start-fb-mcp.ps1.)
#>
param([switch]$WithMcpAutostart)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$psExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $psExe) { $psExe = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
if (-not $psExe) { throw "No PowerShell executable found." }

function Register-OpenEyeTask {
  param($Name, $ScriptPath, $Triggers, $Description, $TimeLimitMinutes)
  $action = New-ScheduledTaskAction -Execute $psExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" `
    -WorkingDirectory $root
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $TimeLimitMinutes)
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers `
    -Settings $settings -Description $Description -Force | Out-Null
  Write-Host "Registered task: $Name"
}

Register-OpenEyeTask -Name 'OpenEye Marketplace Scan' `
  -ScriptPath (Join-Path $PSScriptRoot 'scan.ps1') `
  -Triggers @(
    (New-ScheduledTaskTrigger -Daily -At '8:00AM'),
    (New-ScheduledTaskTrigger -Daily -At '12:00PM'),
    (New-ScheduledTaskTrigger -Daily -At '6:00PM')
  ) `
  -Description 'Scan Facebook Marketplace for underpriced deals vs eBay sold comps.' `
  -TimeLimitMinutes 30

if ($WithMcpAutostart) {
  Register-OpenEyeTask -Name 'OpenEye FB MCP' `
    -ScriptPath (Join-Path $PSScriptRoot 'start-fb-mcp.ps1') `
    -Triggers @((New-ScheduledTaskTrigger -AtLogOn)) `
    -Description 'Keep the Facebook Marketplace MCP server running for OpenEye scans.' `
    -TimeLimitMinutes 0
}

Write-Host ""
Write-Host "Done. Scans run 8am / 12pm / 6pm daily (only while you are logged on)."
if (-not $WithMcpAutostart) {
  Write-Host "Reminder: the FB MCP (scripts/start-fb-mcp.ps1) must be running at those times."
  Write-Host "Tip: re-run with -WithMcpAutostart to launch it automatically at logon."
}
