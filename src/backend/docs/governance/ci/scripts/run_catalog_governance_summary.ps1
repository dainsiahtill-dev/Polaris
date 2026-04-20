[CmdletBinding()]
param(
    [string]$Workspace = ".",
    [ValidateSet("audit-only", "fail-on-new", "hard-fail")]
    [string]$Mode = "audit-only",
    [int]$Top = 10
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$reportPath = Join-Path ([System.IO.Path]::GetTempPath()) ("catalog_governance_report_" + [guid]::NewGuid().ToString("N") + ".json")

try {
    python (Join-Path $scriptRoot "run_catalog_governance_gate.py") --workspace $Workspace --mode $Mode --report $reportPath | Out-Null
    python (Join-Path $scriptRoot "summarize_catalog_governance_gate.py") --input $reportPath --top $Top
}
finally {
    if (Test-Path $reportPath) {
        Remove-Item $reportPath -Force -ErrorAction SilentlyContinue
    }
}
