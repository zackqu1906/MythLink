# Stroke, touchpad and trial tests in parallel (about 1-2 minutes).
# Extra arguments go to pytest, e.g.  .\scripts\test-fast.ps1 --lf   or   -k menu
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".runtime\venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { Write-Error "Project Python is missing. Run scripts\setup.ps1 first."; exit 1 }
$Workers = [Math]::Min([Environment]::ProcessorCount, 12)
Push-Location $Root
try {
    $Files = @(Get-ChildItem tests -Filter "test_*.py" | Where-Object {
        $_.Name -match '^test_(stroke_|touchpad_|ui_touchpad|native_touchpad|complete_stroke_input)'
    } | ForEach-Object { "tests/" + $_.Name })
    & $Python -m pytest -n $Workers --dist load @Files @args
    exit $LASTEXITCODE
} finally { Pop-Location }
