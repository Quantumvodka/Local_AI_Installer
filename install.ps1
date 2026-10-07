# One-line installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main/install.ps1 | iex
# Options: set them first, e.g.   $env:LAI_ARGS = "--dry-run"; irm ... | iex
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$raw = "https://raw.githubusercontent.com/Quantumvodka/Local_AI_Installer/main"
$env:PYTHONUTF8 = "1"

function Test-Python($exe) {
  try {
    $v = & $exe -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
    return ([int]$v -ge 308)
  } catch { return $false }
}

function Find-Python {
  foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    # The Microsoft Store "python" shortcut exists on many PCs but is not a real Python; Test-Python rejects it.
    if ($cmd -and (Test-Python $cmd.Source)) { return $cmd.Source }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "Python not found - fetching a private copy (nothing system-wide changes)..."
  $uvdir = Join-Path $env:USERPROFILE ".local_ai_installer\uv"
  New-Item -ItemType Directory -Force -Path $uvdir | Out-Null
  $env:UV_UNMANAGED_INSTALL = $uvdir
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $uv = Join-Path $uvdir "uv.exe"
  & $uv python install 3.12
  $py = (& $uv python find 3.12 | Out-String).Trim()
}

$tmp = Join-Path $env:TEMP "local_ai_installer.py"
Invoke-WebRequest "$raw/local_ai_installer.py" -OutFile $tmp -UseBasicParsing
$extra = @()
if ($env:LAI_ARGS) { $extra = $env:LAI_ARGS -split " " }
& $py $tmp @extra
