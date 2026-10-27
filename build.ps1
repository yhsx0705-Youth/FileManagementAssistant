$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
$appName = [string]::Concat([char]0x6587, [char]0x4EF6, [char]0x7BA1, [char]0x7406, [char]0x8F85, [char]0x52A9, [char]0x5668)
$iconAssets = Join-Path $PSScriptRoot 'quick_access\assets'
python -m PyInstaller --noconfirm --clean --onefile --windowed --paths '.vendor' `
    --collect-all tkinterdnd2 --icon (Join-Path $iconAssets 'app.ico') `
    --add-data "$iconAssets;quick_access/assets" `
    --name $appName --distpath '.' --workpath 'build' main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Output 'Build complete.'
