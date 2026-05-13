$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell

function New-Shortcut {
    param(
        [string]$Name,
        [string]$Target,
        [string]$Description
    )
    $targetPath = Join-Path $projectRoot $Target
    if (-not (Test-Path $targetPath)) {
        throw "Target not found: $targetPath"
    }

    $shortcutPath = Join-Path $desktop $Name
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = $Description
    $shortcut.WindowStyle = 1
    $icon = Join-Path $projectRoot "assets\nanami_icon.ico"
    if (-not (Test-Path $icon)) {
        $icon = Join-Path $projectRoot ".venv\Scripts\python.exe"
    }
    if (Test-Path $icon) {
        $shortcut.IconLocation = "$icon,0"
    }
    $shortcut.Save()
    Write-Host "Created: $shortcutPath"
}

New-Shortcut -Name "Shinsekai 启动器.lnk" -Target "启动.bat" -Description "打开 Shinsekai 启动菜单"
New-Shortcut -Name "Shinsekai MVP2.lnk" -Target "启动MVP2.bat" -Description "启动 Shinsekai MVP2 Codex 助手"
New-Shortcut -Name "Shinsekai 聊天.lnk" -Target "启动聊天.bat" -Description "启动 Shinsekai 聊天主窗"
New-Shortcut -Name "Shinsekai 设置中心.lnk" -Target "启动设置中心.bat" -Description "启动 Shinsekai 设置中心"

Write-Host "桌面快捷方式已创建。"
