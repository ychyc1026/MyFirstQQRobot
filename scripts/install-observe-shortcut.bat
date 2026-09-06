@echo off
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-observe.ps1" -InstallShortcut -SkipStart
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" echo Shortcut install failed.
pause
exit /b %EXITCODE%
