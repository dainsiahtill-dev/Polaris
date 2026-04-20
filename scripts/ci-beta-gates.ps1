param(
  [string]$Workspace = ".",
  [string]$Output = "",
  [switch]$DryRun,
  [switch]$FullElectron
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "ci-beta-gates.py"
if (-not (Test-Path $scriptPath)) {
  throw "Missing script: $scriptPath"
}

$argsList = @($scriptPath, "--workspace", $Workspace)
if ($Output) {
  $argsList += @("--output", $Output)
}
if ($DryRun) {
  $argsList += "--dry-run"
}
if ($FullElectron) {
  $argsList += "--full-electron"
}

& python @argsList
exit $LASTEXITCODE
