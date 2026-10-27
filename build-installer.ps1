param([switch]$Force)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$version = '1.3.9'
$iconAssets = Join-Path $PSScriptRoot 'quick_access\assets'
$appName = [string]::Concat([char]0x6587, [char]0x4EF6, [char]0x7BA1, [char]0x7406,
    [char]0x8F85, [char]0x52A9, [char]0x5668)
$buildRoot = Join-Path $PSScriptRoot ('build\release-stage-' + $version + '-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $buildRoot 'app'
$release = Join-Path $PSScriptRoot ('release\' + $version)
$setupName = "FileManagementAssistant-Setup-$version-x64.exe"
$setupPath = Join-Path $release $setupName
$legacySetupName = "$appName-安装程序-$version-x64.exe"
$knownOutputs = @($setupName, $legacySetupName, '安装说明.txt', 'SHA256SUMS.txt')
$makensis = Get-Command makensis -ErrorAction SilentlyContinue

if (-not $makensis) {
    throw '找不到 makensis。请安装 NSIS 3，并确保 makensis.exe 位于 PATH 中。'
}
if (Test-Path -LiteralPath $release) {
    $existing = @(Get-ChildItem -Force -LiteralPath $release)
    $unexpected = @($existing | Where-Object { $_.Name -notin $knownOutputs })
    if ($unexpected.Count -gt 0) {
        throw "发布目录包含非本构建生成的内容，为避免覆盖，已停止：$release"
    }
    if ($existing.Count -gt 0 -and -not $Force) {
        throw "发布文件已存在。确认要替换本版本输出时，请使用 -Force。"
    }
    if ($Force) {
        foreach ($name in $knownOutputs) {
            $target = Join-Path $release $name
            if (Test-Path -LiteralPath $target -PathType Leaf) {
                Remove-Item -LiteralPath $target
            }
        }
    }
} else {
    New-Item -ItemType Directory -Path $release | Out-Null
}

python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }

New-Item -ItemType Directory -Path $stage | Out-Null
python -m PyInstaller --noconfirm --clean --onefile --windowed --paths '.vendor' `
    --collect-all tkinterdnd2 --icon (Join-Path $iconAssets 'app.ico') `
    --add-data "$iconAssets;quick_access/assets" --name $appName --distpath $stage `
    --workpath (Join-Path $buildRoot 'work') --specpath $buildRoot main.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }

& $makensis.Source "/DAPP_VERSION=$version" "/DAPP_STAGE=$stage" `
    "/DOUTPUT_DIR=$release" (Join-Path $PSScriptRoot 'installer.nsi')
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

$notes = @"
$appName $version (Windows x64)

系统要求：Windows 8.1 或更新版本，64 位。
安装方式：双击安装程序，按向导完成安装。默认仅安装到当前 Windows 用户。
卸载方式：从 Windows「设置 > 应用」卸载，或运行安装目录中的 Uninstall.exe；选择创建开始菜单快捷方式时，也可从开始菜单卸载。
卸载会移除程序、快捷方式和应用注册项，默认保留收藏与设置。卸载时可选择同时删除用户数据；此操作不可恢复。
安装程序不包含开发源码、测试文件或发布者的个人入口数据。
此版本尚未使用代码签名证书签名，首次下载时 Windows 可能显示 SmartScreen 提示。

发布者：云鹤sx
"@
Set-Content -LiteralPath (Join-Path $release '安装说明.txt') -Value $notes -Encoding utf8
$hash = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $release 'SHA256SUMS.txt') `
    -Value "$hash  $setupName" -Encoding ascii

Get-ChildItem -LiteralPath $release -File | Select-Object Name,Length,LastWriteTime
Write-Output 'Installer release complete.'
