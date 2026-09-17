@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m unittest discover -s tests -v
pause
