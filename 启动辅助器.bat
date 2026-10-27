@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "文件管理辅助器.exe" (
    start "" "文件管理辅助器.exe"
    exit /b
)
python main.py
if errorlevel 1 pause
