# Rebuild installer with splash + silent engine + stable engine token.
# From repo root:
#   powershell -ExecutionPolicy Bypass -File apps\desktop\scripts\rebuild-desktop-fixes.ps1

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$DesktopRoot = Resolve-Path (Join-Path $Here '..')
$RepoRoot = Resolve-Path (Join-Path $DesktopRoot '../..')
$EngineScript = Join-Path $RepoRoot 'backend\scripts\desktop\build_engine_windows.ps1'

Write-Host '======== AChat desktop fix rebuild ========'
Write-Host ('Repo: ' + $RepoRoot)

Write-Host ''
Write-Host '[0/4] Stop old processes...'
Get-Process achat-desktop, achat-engine -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match 'achat-engine|app\.desktop\.cli') } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host ''
Write-Host '[1/4] Engine sidecar (windowed + SSL)...'
powershell -ExecutionPolicy Bypass -File $EngineScript
$EngineExe = Join-Path $DesktopRoot 'src-tauri\resources\engine\achat-engine.exe'
if (-not (Test-Path $EngineExe)) {
  throw ('Engine missing: ' + $EngineExe)
}

Write-Host ''
Write-Host '[2/4] Full static UI (token wait + auth fix)...'
Push-Location $RepoRoot
try {
  pnpm desktop:build-ui
}
finally {
  Pop-Location
}

Push-Location $DesktopRoot
try {
  pnpm build:ui
  $uiIndex = Join-Path $DesktopRoot 'src-tauri\resources\ui\index.html'
  if (Test-Path $uiIndex) {
    $raw = Get-Content -Path $uiIndex -Raw -ErrorAction SilentlyContinue
    if ($raw -and $raw.Contains('pnpm dev') -and $raw.Contains('localhost:3000')) {
      throw 'UI still looks like placeholder - abort'
    }
  }
  pnpm check:infra
}
finally {
  Pop-Location
}

Write-Host ''
Write-Host '[3/4] Tauri NSIS (splash + no console + stable token)...'
Push-Location $DesktopRoot
try {
  pnpm build
}
finally {
  Pop-Location
}

$NsisDir = Join-Path $DesktopRoot 'src-tauri\target\release\bundle\nsis'
Write-Host ''
Write-Host '======== Done ========'
Write-Host 'Installer:'
if (Test-Path $NsisDir) {
  Get-ChildItem $NsisDir -Filter '*.exe' | ForEach-Object {
    Write-Host ('  -> ' + $_.FullName)
  }
}
else {
  Write-Host ('  nsis dir missing: ' + $NsisDir)
}

Write-Host ''
Write-Host 'Next steps:'
Write-Host '  1. Kill AChat processes'
Write-Host '  2. Uninstall old AChat or overwrite install'
Write-Host '  3. Run the new AChat setup exe'
Write-Host '  4. Login admin@local / 123456, quit, reopen, login again'
Write-Host ''
