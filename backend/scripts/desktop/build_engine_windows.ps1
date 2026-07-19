# Build Windows local-engine package (one-folder PyInstaller).
# Spike decision: prefer PyInstaller one-folder for v1 (simpler dep bundling than
# hand-maintaining embeddable CPython + venv). Output: dist/achat-engine/
#
# Usage (from repo root, with backend venv active):
#   powershell -File backend/scripts/desktop/build_engine_windows.ps1

$ErrorActionPreference = "Stop"
$BackendRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")
$RepoRoot = Resolve-Path (Join-Path $BackendRoot "..")
$OutDir = Join-Path $RepoRoot "apps/desktop/src-tauri/resources/engine"
$WorkDir = Join-Path $BackendRoot "build/desktop-engine"

Write-Host "==> Backend: $BackendRoot"
Write-Host "==> Output : $OutDir"

Push-Location $BackendRoot
try {
  python -m pip install --upgrade pip
  python -m pip install pyinstaller
  if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir | Out-Null }

  # One-folder keeps shared libs beside the exe (better for native deps).
  python -m PyInstaller `
    --noconfirm `
    --clean `
    --name achat-engine `
    --paths $BackendRoot `
    --distpath (Join-Path $WorkDir "dist") `
    --workpath (Join-Path $WorkDir "work") `
    --specpath $WorkDir `
    --collect-all uvicorn `
    --collect-all fastapi `
    --hidden-import app.desktop.cli `
    (Join-Path $BackendRoot "scripts/desktop/run_engine.py")

  if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
  New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
  Copy-Item -Recurse -Force (Join-Path $WorkDir "dist/achat-engine/*") $OutDir
  Write-Host "==> Engine package ready at $OutDir"
  Write-Host "    Entry: achat-engine.exe serve --bind 127.0.0.1 ..."
}
finally {
  Pop-Location
}
