#requires -Version 5.1
<#
  Runs ONE marketplace deal scan headless. Loads .env, then invokes Claude Code against
  CLAUDE.md with only the tools the scan needs allow-listed, so it runs unattended.

  PREREQS:
    1. scripts/start-fb-mcp.ps1 is already running and logged in to Facebook.
    2. You have run Claude interactively in this folder ONCE and approved the two project
       MCP servers (facebook-marketplace, ebay-sold-comps). Headless runs can't approve them.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot\_load-env.ps1"

foreach ($v in 'FB_LOCATION_ID') {
  if (-not [Environment]::GetEnvironmentVariable($v)) { throw "$v is not set in .env" }
}
if ([Environment]::GetEnvironmentVariable('FB_LOCATION_ID') -like 'REPLACE_*') {
  throw "FB_LOCATION_ID is still the placeholder. Set your Seattle-area numeric ID in .env (see README)."
}

New-Item -ItemType Directory -Force -Path (Join-Path $root 'reports') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root 'state')   | Out-Null

$prompt = @'
Run the Facebook Marketplace deal scan exactly as specified in CLAUDE.md.
Use config/watchlist.yaml and FB_LOCATION_ID from the environment.
Write the ranked report to reports/ (both .md and .json) and update state/seen_listings.json.
Do not contact sellers or take any action beyond producing the report.
'@

# Server-level MCP grants allow all tools from each server (facebook-marketplace +
# the local ebay-sold-comps). acceptEdits auto-confirms the report file writes.
$allowed = @(
  'mcp__facebook-marketplace',
  'mcp__ebay-sold-comps',
  'Read', 'Write', 'Glob'
) -join ','

Push-Location $root
try {
  claude -p $prompt --allowedTools $allowed --permission-mode acceptEdits
} finally {
  Pop-Location
}
