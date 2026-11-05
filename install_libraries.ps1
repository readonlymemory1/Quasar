$ErrorActionPreference = "Stop"

$requirements = Join-Path $PSScriptRoot "requirements.txt"
if (-not (Test-Path $requirements -PathType Leaf)) {
    throw "requirements.txt was not found: $requirements"
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python was not found. Install Python and ensure python.exe is on PATH."
}

Write-Host "Installing Python libraries from requirements.txt..."
& $python.Source -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Library installation failed (pip exit code $LASTEXITCODE)."
}

Write-Host "All Python libraries in requirements.txt were installed."
