# One-line installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$raw = "https://raw.githubusercontent.com/quantumvodka/local_ai_installer/main"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Host "Python 3 not found. Installing via winget..."
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  Write-Host "Python installed. Close and reopen PowerShell, then run this command again."
  return
}
$tmp = Join-Path $env:TEMP "local_ai_installer.py"
Invoke-WebRequest "$raw/local_ai_installer.py" -OutFile $tmp
& $py.Source $tmp
