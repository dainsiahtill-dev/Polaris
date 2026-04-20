param(
  [string]$Workspace = ".",
  [switch]$Full,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "run_factory_e2e_smoke.py"
if (-not (Test-Path $scriptPath)) {
  throw "Missing script: $scriptPath"
}

$argsList = @($scriptPath, "--workspace", $Workspace)
if ($Full) {
  $argsList += "--full"
}
if ($DryRun) {
  $argsList += "--dry-run"
}

& python @argsList
exit $LASTEXITCODE
