# One-line installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$raw = "https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main"

function Find-Python {
  foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
      # The Microsoft Store "python" stub exists but fails to run; check it really works.
      try { $v = & $cmd.Source --version 2>&1; if ($v -match "Python 3") { return $cmd.Source } } catch {}
    }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "Python 3 not found. Installing via winget..."
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  # Pick up the new PATH without restarting the shell.
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
  $py = Find-Python
  if (-not $py) { Write-Host "Python installed, but please close and reopen this window and run the installer again."; return }
}
$tmp = Join-Path $env:TEMP "local_ai_installer.py"
Invoke-WebRequest "$raw/local_ai_installer.py" -OutFile $tmp
& $py $tmp
