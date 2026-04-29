# rcpilot — cockpit control sender (Windows PowerShell convenience wrapper).
#
# Wraps `python -m rcpilot.cockpit.control_sender` for cockpit operators who
# don't want to remember the module path. Forwards all arguments through.
#
# Usage:
#   .\start_sender.ps1                       # all defaults
#   .\start_sender.ps1 -- --jetson 10.0.0.42 # pass through to the Python entry
#   .\start_sender.ps1 -- --rate-hz 125

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python is not on PATH. Install Python 3.10+ from python.org and check 'Add to PATH'."
}

# Run from the repo root so relative config paths resolve correctly.
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $repoRoot
try {
    python -m rcpilot.cockpit.control_sender @Args
} finally {
    Pop-Location
}
