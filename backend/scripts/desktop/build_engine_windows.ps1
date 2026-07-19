# Build Windows local-engine package (PyInstaller one-folder).
# Output: apps/desktop/src-tauri/resources/engine/achat-engine.exe (+ deps)
#
# Usage (from repo root, Windows PowerShell):
#   powershell -ExecutionPolicy Bypass -File backend\scripts\desktop\build_engine_windows.ps1
#
# Prefer backend\.venv if present.

$ErrorActionPreference = "Stop"
$BackendRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")
$RepoRoot = Resolve-Path (Join-Path $BackendRoot "..")
$OutDir = Join-Path $RepoRoot "apps/desktop/src-tauri/resources/engine"
$WorkDir = Join-Path $BackendRoot "build/desktop-engine"

$VenvPython = Join-Path $BackendRoot ".venv/Scripts/python.exe"
if (Test-Path $VenvPython) {
  $Python = $VenvPython
} else {
  $Python = "python"
}

Write-Host "==> Python : $Python"
Write-Host "==> Backend: $BackendRoot"
Write-Host "==> Output : $OutDir"

Push-Location $BackendRoot
try {
  & $Python -m pip install --upgrade pip
  & $Python -m pip install "pyinstaller>=6.0"
  if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir | Out-Null }

  # One-folder keeps shared libs beside the exe (better for native deps).
  # SSL/_ssl often breaks on Windows one-folder if not collected explicitly.
  # --noconsole / --windowed: no black cmd window when shell spawns the engine.
  & $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name achat-engine `
    --paths $BackendRoot `
    --distpath (Join-Path $WorkDir "dist") `
    --workpath (Join-Path $WorkDir "work") `
    --specpath $WorkDir `
    --collect-all uvicorn `
    --collect-all fastapi `
    --collect-all starlette `
    --collect-all pydantic `
    --collect-all sqlalchemy `
    --collect-all certifi `
    --collect-submodules ssl `
    --hidden-import ssl `
    --hidden-import _ssl `
    --hidden-import certifi `
    --hidden-import app.desktop.cli `
    --hidden-import app.desktop.config `
    --hidden-import app.desktop.runtime `
    --hidden-import app.desktop.middleware `
    --hidden-import app.desktop.static_ui `
    --hidden-import app.main `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols `
    --hidden-import uvicorn.protocols.http `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan `
    --hidden-import uvicorn.lifespan.on `
    (Join-Path $BackendRoot "scripts/desktop/run_engine.py")

  if (-not (Test-Path (Join-Path $WorkDir "dist/achat-engine/achat-engine.exe"))) {
    throw "PyInstaller did not produce achat-engine.exe"
  }

  if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
  New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
  Copy-Item -Recurse -Force (Join-Path $WorkDir "dist/achat-engine/*") $OutDir

  # Conda / some CPython builds ship OpenSSL in Library\bin; PyInstaller often
  # packs a wrong/smaller libssl that makes `_ssl` fail at runtime
  # (ImportError: DLL load failed while importing _ssl).
  $Internal = Join-Path $OutDir "_internal"
  $PyPrefix = & $Python -c "import sys; print(sys.base_prefix)"
  $SslCandidates = @(
    (Join-Path $PyPrefix "Library\bin"),
    (Join-Path $PyPrefix "DLLs"),
    (Join-Path $PyPrefix "Library\lib"),
    $PyPrefix
  )
  $SslNames = @(
    "libssl-3-x64.dll",
    "libcrypto-3-x64.dll",
    "libssl-3.dll",
    "libcrypto-3.dll",
    "ffi.dll",
    "ffi-8.dll",
    "zlib.dll",
    "_ssl.pyd"
  )
  foreach ($name in $SslNames) {
    foreach ($dir in $SslCandidates) {
      $src = Join-Path $dir $name
      if (Test-Path $src) {
        Copy-Item -Force $src (Join-Path $Internal $name)
        # Also next to the exe (Windows DLL search path)
        Copy-Item -Force $src (Join-Path $OutDir $name)
        Write-Host "==> SSL fix: copied $src"
        break
      }
    }
  }

  # Anaconda / some Conda builds ship private UCRT forwarders (api-ms-win-*.dll +
  # ucrtbase.dll) into _internal. On Windows 10/11 those stubs break Winsock
  # initialization inside the frozen app:
  #   OSError: [WinError 10106] WSAEPROVIDERFAILEDINIT
  # (socket.socket() fails → allocate_port / uvicorn never bind → shell health timeout).
  # Prefer the system UCRT that ships with Windows.
  if (Test-Path $Internal) {
    $removed = 0
    Get-ChildItem -Path $Internal -Filter "api-ms-win-*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
      Remove-Item -Force $_.FullName
      $removed++
    }
    $Ucrt = Join-Path $Internal "ucrtbase.dll"
    if (Test-Path $Ucrt) {
      Remove-Item -Force $Ucrt
      $removed++
    }
    if ($removed -gt 0) {
      Write-Host "==> Winsock fix: removed $removed Anaconda UCRT forwarder DLL(s) from _internal (use system UCRT)"
    }
  }

  # Smoke: packaged engine must import ssl (fails fast if OpenSSL still wrong)
  $SmokePy = @"
import os, sys
from pathlib import Path
root = Path(r'$OutDir')
internal = root / '_internal'
os.environ['PATH'] = str(internal) + os.pathsep + str(root) + os.pathsep + os.environ.get('PATH', '')
if hasattr(os, 'add_dll_directory'):
    for p in (internal, root):
        if p.is_dir():
            try: os.add_dll_directory(str(p))
            except Exception: pass
# Prefer loading via the bundled interpreter if present
print('smoke: checking _ssl load...')
import ctypes
ssl_pyd = internal / '_ssl.pyd'
if not ssl_pyd.is_file():
    raise SystemExit('smoke failed: _ssl.pyd missing under _internal')
ctypes.WinDLL(str(ssl_pyd))
print('smoke: _ssl.pyd LOAD_OK')
print('smoke: checking Winsock / socket...')
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(('127.0.0.1', 0))
port = s.getsockname()[1]
s.close()
print(f'smoke: socket BIND_OK port={port}')
"@
  $SmokeFile = Join-Path $WorkDir "smoke_ssl.py"
  Set-Content -Path $SmokeFile -Value $SmokePy -Encoding UTF8
  & $Python $SmokeFile
  if ($LASTEXITCODE -ne 0) {
    throw "Engine SSL/socket smoke failed — fix OpenSSL / UCRT DLLs before packaging installer"
  }

  # End-to-end smoke: frozen exe must allocate a port (catches WinError 10106).
  $SmokeData = Join-Path $WorkDir "smoke-data"
  if (Test-Path $SmokeData) { Remove-Item -Recurse -Force $SmokeData }
  New-Item -ItemType Directory -Path $SmokeData | Out-Null
  $EngineExe = Join-Path $OutDir "achat-engine.exe"
  $InfraDefault = Join-Path $RepoRoot "apps/desktop/src-tauri/infra.default.json"
  $SmokeArgs = @(
    "serve",
    "--bind", "127.0.0.1",
    "--port", "0",
    "--data-dir", $SmokeData,
    "--engine-token", "smoke-token-0123456789abcdef0123456789abcdef"
  )
  if (Test-Path $InfraDefault) {
    $SmokeArgs += @("--infra-config", $InfraDefault)
  }
  $SmokeOut = Join-Path $WorkDir "smoke_serve_out.txt"
  $SmokeErr = Join-Path $WorkDir "smoke_serve_err.txt"
  Write-Host "==> Smoke: launching packaged achat-engine.exe serve ..."
  $proc = Start-Process -FilePath $EngineExe -ArgumentList $SmokeArgs `
    -WorkingDirectory $OutDir -NoNewWindow -PassThru `
    -RedirectStandardOutput $SmokeOut -RedirectStandardError $SmokeErr
  $ok = $false
  $deadline = (Get-Date).AddSeconds(45)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $SmokeOut) {
      $txt = Get-Content $SmokeOut -Raw -ErrorAction SilentlyContinue
      if ($txt -match "ENGINE_PORT=(\d+)") {
        $ok = $true
        Write-Host "==> Smoke: ENGINE_PORT=$($Matches[1])"
        break
      }
    }
    if ($proc.HasExited) { break }
    Start-Sleep -Milliseconds 250
  }
  if (-not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    try { $proc.WaitForExit(5000) | Out-Null } catch {}
  }
  if (-not $ok) {
    Write-Host "--- smoke stdout ---"
    if (Test-Path $SmokeOut) { Get-Content $SmokeOut }
    Write-Host "--- smoke stderr ---"
    if (Test-Path $SmokeErr) { Get-Content $SmokeErr }
    throw "Engine serve smoke failed (no ENGINE_PORT). Often WinError 10106 from bundled Anaconda UCRT DLLs."
  }

  # Keep a marker so empty-dir checks don't delete structure
  Set-Content -Path (Join-Path $OutDir ".packaged") -Value (Get-Date -Format o)

  Write-Host "==> Engine package ready at $OutDir"
  Write-Host "    Entry: achat-engine.exe serve --bind 127.0.0.1 --port 0 --data-dir ... --engine-token ... --infra-config ..."
}
finally {
  Pop-Location
}
