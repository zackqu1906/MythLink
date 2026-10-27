# Whole suite in parallel; run before committing.
# Extra arguments go to pytest, e.g.  .\scripts\test-full.ps1 --lf
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { Write-Error "Project Python is missing. Run scripts\setup.ps1 first."; exit 1 }
$Workers = [Math]::Min([Environment]::ProcessorCount, 12)
Push-Location $Root
try {
    # One broken module must not hide the results of the other files.
    & $Python -m pytest -n $Workers --dist load --continue-on-collection-errors -rfEs @args
    exit $LASTEXITCODE
} finally { Pop-Location }
