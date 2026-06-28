#requires -Version 5.1
<#
  Starts the Facebook Marketplace MCP server (fisheyes/mcp-facebook-market-place) locally.

  FIRST RUN: a Chromium window opens - log in to Facebook and dismiss any warnings so the
  session persists in the MCP's browser profile. Keep this process running while you scan;
  the scan task expects the server reachable at FB_MCP_URL (default http://127.0.0.1:8000/mcp).
#>
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_load-env.ps1"

if (-not $env:FB_MCP_DIR) {
  throw "FB_MCP_DIR is not set. Copy .env.example to .env and set it to your clone of mcp-facebook-market-place."
}
if (-not (Test-Path -LiteralPath $env:FB_MCP_DIR)) {
  throw "FB_MCP_DIR does not exist: $env:FB_MCP_DIR"
}

Write-Host "Starting Facebook Marketplace MCP from $env:FB_MCP_DIR ..."
Write-Host "(Leave this window open. Ctrl+C to stop.)"
Push-Location $env:FB_MCP_DIR
try {
  uv run python server.py
} finally {
  Pop-Location
}
