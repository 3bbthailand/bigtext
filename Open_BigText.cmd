@echo off
setlocal
cd /d "%~dp0"
if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" (
  start "" "%LocalAppData%\Programs\Python\Python311\pythonw.exe" "%~dp0BigText.pyw" %*
  exit /b 0
)
py -3 "%~dp0bigtext.py" gui %*
if errorlevel 1 pause
