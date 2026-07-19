@echo off
setlocal EnableExtensions
call "D:\Visual Studio\product\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 (
  echo vcvars64 failed
  exit /b 1
)
set "CARGO_HOME=C:\Users\mmyy\.cargo"
set "PATH=C:\Users\mmyy\.cargo\bin;D:\Visual Studio\product\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64;%PATH%"
echo [with-msvc] cl=
where cl
echo [with-msvc] cargo=
where cargo

REM Usage:
REM   with-msvc.bat check --manifest-path ...
REM   with-msvc.bat run pnpm dev
if /I "%~1"=="run" goto run_mode
goto cargo_mode

:run_mode
shift
if "%~1"=="" (
  echo usage: with-msvc.bat run ^<command^> [args...]
  echo example: with-msvc.bat run pnpm dev
  exit /b 1
)
echo [with-msvc] run: %1 %2 %3 %4 %5 %6 %7 %8 %9
%1 %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:cargo_mode
if exist "C:\Users\mmyy\.cargo\bin\cargo.exe" (
  C:\Users\mmyy\.cargo\bin\cargo.exe %*
) else (
  cargo %*
)
exit /b %ERRORLEVEL%
