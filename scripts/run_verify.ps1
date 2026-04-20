param(
  [string]$ProjectRoot = "..",
  [string[]]$ChangedFiles = @(),
  [ValidateSet("json", "text")]
  [string]$Output = "json",
  [switch]$EvidenceRun,
  [switch]$FastLoop
)

$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path $ProjectRoot).Path

function Get-VerifyCommands {
  param(
    [string]$Root,
    [string[]]$Files
  )

  $commands = New-Object System.Collections.Generic.List[string]

  $pythonFiles = @($Files | Where-Object {
      $_ -and ($_ -match "\.pyi?$")
  })
  if ($pythonFiles.Count -gt 0) {
    foreach ($file in $pythonFiles) {
      $fullPath = Join-Path $Root $file
      if (Test-Path $fullPath) {
        $commands.Add(("python -m py_compile `"{0}`"" -f $fullPath))
      }
    }
    return $commands
  }

  $hasPythonProject = (Test-Path (Join-Path $Root "pyproject.toml")) -or `
    (Test-Path (Join-Path $Root "pytest.ini")) -or `
    (Test-Path (Join-Path $Root "requirements.txt"))
  if ($hasPythonProject) {
    $commands.Add("python -m pytest -q")
  }

  $hasNodeProject = Test-Path (Join-Path $Root "package.json")
  if ($hasNodeProject) {
    $commands.Add("npm run -s test --if-present")
  }

  return $commands
}

$mode = if ($FastLoop.IsPresent) { "fast_loop" } else { "evidence_run" }
$commands = Get-VerifyCommands -Root $projectDir -Files $ChangedFiles
$results = New-Object System.Collections.Generic.List[object]
$ok = $true

Push-Location $projectDir
try {
  foreach ($cmd in $commands) {
    $start = Get-Date
    $outputLines = @()
    try {
      $outputLines = @(cmd /c $cmd 2>&1)
      $exitCode = $LASTEXITCODE
    } catch {
      $outputLines = @($_.Exception.Message)
      $exitCode = 1
    }
    $elapsedMs = [int]((Get-Date) - $start).TotalMilliseconds
    if ($exitCode -ne 0) {
      $ok = $false
    }
    $results.Add([ordered]@{
        command = $cmd
        exit_code = $exitCode
        duration_ms = $elapsedMs
        output = (($outputLines -join "`n").Trim())
      })
    if ($exitCode -ne 0 -and $FastLoop.IsPresent) {
      break
    }
  }
}
finally {
  Pop-Location
}

$status = if ($commands.Count -eq 0) { "skipped" } elseif ($ok) { "success" } else { "failed" }
$payload = [ordered]@{
  ok = $ok
  status = $status
  mode = $mode
  evidence_run = [bool]$EvidenceRun.IsPresent
  fast_loop = [bool]$FastLoop.IsPresent
  project_root = $projectDir
  commands = @($commands)
  results = @($results)
}

if ($Output -eq "text") {
  if ($commands.Count -eq 0) {
    Write-Output "No local verify commands matched; skipped."
  } else {
    foreach ($row in $results) {
      Write-Output ("[{0}] {1}" -f $row.exit_code, $row.command)
      if ($row.output) {
        Write-Output $row.output
      }
    }
  }
} else {
  $payload | ConvertTo-Json -Depth 8
}

if ($status -eq "failed") { exit 1 }
exit 0
