# Loads KEY=VALUE pairs from the repo-root .env into the current PROCESS environment,
# so child processes (claude, uv) and ${VAR} interpolation in .mcp.json can see them.
# Ignores comments/blank lines; strips matching surrounding single/double quotes.
$envPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
  Write-Warning ".env not found at $envPath - copy .env.example to .env and fill it in."
  return
}
Get-Content -LiteralPath $envPath | ForEach-Object {
  $line = $_.Trim()
  if ($line -eq '' -or $line.StartsWith('#')) { return }
  $idx = $line.IndexOf('=')
  if ($idx -lt 1) { return }
  $name  = $line.Substring(0, $idx).Trim()
  $value = $line.Substring($idx + 1).Trim()
  if ($value.Length -ge 2) {
    $first = $value.Substring(0, 1)
    $last  = $value.Substring($value.Length - 1, 1)
    if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
      $value = $value.Substring(1, $value.Length - 2)
    }
  }
  [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}
