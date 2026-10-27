@echo off
chcp 65001 >nul
powershell.exe -NoProfile -Command "$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\文件管理辅助器'; $app=Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue; if ($app -and $app.InstallLocation) { $uninstaller=Join-Path $app.InstallLocation 'Uninstall.exe'; if (Test-Path -LiteralPath $uninstaller) { Start-Process -FilePath $uninstaller; exit 0 } }; Write-Host 'No installed copy was found. Portable copies can be removed by deleting their application folder.'; Read-Host 'Press Enter to close'"
