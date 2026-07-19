# One-shot internal Windows package build (engine + ui + tauri nsis).
# Run from repo root OR apps/desktop.
#
#   powershell -ExecutionPolicy Bypass -File apps\desktop\scripts\build-internal.ps1

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$DesktopRoot = Resolve-Path (Join-Path $Here "..")
$RepoRoot = Resolve-Path (Join-Path $DesktopRoot "../..")
$BackendRoot = Join-Path $RepoRoot "backend"
$EngineScript = Join-Path $BackendRoot "scripts/desktop/build_engine_windows.ps1"

Write-Host "======== AChat internal package ========"
Write-Host "Repo: $RepoRoot"

# 1) Engine sidecar
Write-Host "`n[1/4] Building engine sidecar (PyInstaller)..."
powershell -ExecutionPolicy Bypass -File $EngineScript
$EngineExe = Join-Path $DesktopRoot "src-tauri/resources/engine/achat-engine.exe"
if (-not (Test-Path $EngineExe)) {
  throw "Engine missing after build: $EngineExe"
}

# 2) UI assets
Write-Host "`n[2/4] Building static UI resources..."
Push-Location $DesktopRoot
try {
  pnpm build:ui
  pnpm check:infra
}
finally {
  Pop-Location
}

# 3) Config presence
$Infra = Join-Path $DesktopRoot "src-tauri/infra.default.json"
if (-not (Test-Path $Infra)) {
  throw "Missing $Infra — copy configs/infra.default.dev.json first"
}
Write-Host "[3/4] infra.default.json OK: $Infra"

# 4) Tauri release + NSIS
Write-Host "`n[4/4] tauri build (NSIS)..."
Push-Location $DesktopRoot
try {
  pnpm build
}
finally {
  Pop-Location
}

$NsisDir = Join-Path $DesktopRoot "src-tauri/target/release/bundle/nsis"
Write-Host "`n======== Done ========"
Write-Host "Look for installer under:"
Write-Host "  $NsisDir"
if (Test-Path $NsisDir) {
  Get-ChildItem $NsisDir -Filter *.exe | ForEach-Object { Write-Host "  -> $($_.FullName)" }
}
