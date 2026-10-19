@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py start.py) || (python start.py)