$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements-dev.txt first.' }
& $taskPython -m PyInstaller --noconfirm --clean --onefile --windowed --name OsEasyToolBoxEnhanced --collect-all frida --collect-all flet --exclude-module tkinter --exclude-module pystray --add-data 'oseasy_auto/assets;oseasy_auto/assets' run.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Get-Item -LiteralPath (Join-Path $taskRoot 'dist\OsEasyToolBoxEnhanced.exe') | Select-Object FullName,Length
