# YCH observe starter: YCH listener first, then sibling NapCat QR login.
# Does not enable Qzone, history, or owner reports.
# Allows already-configured shadow (outbound off) or owner_approved/limited_auto/auto (outbound on).
# Named live auto is still conversation-gated in the reply runtime.
# Does not print tokens, API keys, or chat bodies.

param(
    [switch] $InstallShortcut,
    [switch] $SkipStart,
    [switch] $SkipNapCat,
    [switch] $SkipShortcut,
    [switch] $QuickLogin,
    [switch] $WithDashboard,
    [string] $NapCatRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $script:OutputUTF8 = New-Object System.Text.UTF8Encoding $false
} catch {
    $script:OutputUTF8 = [System.Text.Encoding]::UTF8
}

$BotQq = "2000000002"
$OwnerQq = "2000000001"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $NapCatRoot) {
    $NapCatRoot = Join-Path (Split-Path $RepoRoot -Parent) "napcat"
}

function Write-Info([string] $Message) {
    Write-Host $Message
}

function Write-Warn([string] $Message) {
    Write-Host $Message -ForegroundColor Yellow
}

function Write-Fail([string] $Message) {
    Write-Host $Message -ForegroundColor Red
}

function Get-YchPython {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    throw "找不到仓库内虚拟环境的 python.exe。请先在仓库根目录运行 uv sync --extra dev。"
}

function Read-AllowlistedSettings {
    $python = Get-YchPython
    $helper = Join-Path $PSScriptRoot "print_observe_settings.py"
    $raw = & $python $helper
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        throw "读取允许列出的配置失败（未打印密钥）。"
    }
    return $raw | ConvertFrom-Json
}

function Get-ProbeHost([string] $AdminHost) {
    if ($AdminHost -in @("0.0.0.0", "::", "[::]")) {
        return "127.0.0.1"
    }
    return $AdminHost
}

function Test-YchLive([string] $LiveUrl) {
    try {
        $result = Invoke-RestMethod -Uri $LiveUrl -TimeoutSec 2
        return ($result.service -eq "ych-bot" -and $result.status -eq "ok")
    } catch {
        return $false
    }
}

function Wait-YchLive([string] $LiveUrl, [int] $TimeoutSeconds = 75) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-YchLive $LiveUrl) {
            return
        }
        Start-Sleep -Milliseconds 400
    }
    throw "YCH 未在 ${TimeoutSeconds}s 内变为 live。请查看 YCH 窗口中的预检错误。"
}

function Read-Ready([string] $ReadyUrl) {
    return Invoke-RestMethod -Uri $ReadyUrl -TimeoutSec 5
}

function Get-QQExePath {
    $candidates = New-Object System.Collections.Generic.List[string]
    $keyPath = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"
    try {
        $item = Get-ItemProperty -LiteralPath $keyPath -ErrorAction Stop
        $rawValues = @()
        foreach ($name in @("UninstallString", "DisplayIcon", "InstallLocation")) {
            $property = $item.PSObject.Properties[$name]
            if ($property -and $property.Value) {
                $rawValues += [string] $property.Value
            }
        }
        foreach ($raw in $rawValues) {
            $cleaned = $raw.Trim().Trim('"')
            $cleaned = ($cleaned -split ",", 2)[0].Trim().Trim('"')
            if ($cleaned -match '(?i)QQ\.exe$') {
                [void] $candidates.Add($cleaned)
            } elseif (Test-Path -LiteralPath $cleaned -PathType Container -ErrorAction SilentlyContinue) {
                [void] $candidates.Add((Join-Path $cleaned "QQ.exe"))
            } elseif ($cleaned) {
                $parent = Split-Path -Parent $cleaned
                if ($parent) {
                    [void] $candidates.Add((Join-Path $parent "QQ.exe"))
                }
            }
        }
    } catch {
        # Registry lookup is optional; common install paths are tried next.
    }
    foreach ($known in @(
            "${env:ProgramFiles}\Tencent\QQNT\QQ.exe",
            "${env:ProgramFiles(x86)}\Tencent\QQNT\QQ.exe",
            "${env:LOCALAPPDATA}\Programs\Tencent\QQNT\QQ.exe"
        )) {
        if ($known) {
            [void] $candidates.Add($known)
        }
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            return $path
        }
    }
    return $null
}

function Install-ObserveShortcut {
    $bat = Join-Path $PSScriptRoot "start-observe.bat"
    $lnkPath = Join-Path $PSScriptRoot "YCH 观察启动.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($lnkPath)
    $shortcut.TargetPath = $bat
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.WindowStyle = 1
    $shortcut.Description = "YCH observe start: YCH first, then NapCat QR. No outbound."
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $python) {
        $shortcut.IconLocation = "$python,0"
    }
    $shortcut.Save()
    Write-Info "已在 scripts 文件夹创建或更新快捷方式：YCH 观察启动"
}

function Start-YchWindow([string] $PythonExe) {
    $command = @"
Set-Location -LiteralPath '$RepoRoot'
[Console]::Title = 'YCH observe listener'
Write-Host 'YCH 观察监听已启动。关闭本窗口即停止 YCH。当前脚本不会打开外发。'
& '$PythonExe' -m ych_bot
Write-Host 'YCH 已退出。'
Read-Host '按 Enter 关闭窗口'
"@
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $RepoRoot -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-NoExit",
        "-Command", $command
    ) | Out-Null
}

function Start-NapCatWindow([string] $QQExe) {
    $boot = Join-Path $NapCatRoot "NapCatWinBootMain.exe"
    $hook = Join-Path $NapCatRoot "NapCatWinBootHook.dll"
    $patch = Join-Path $NapCatRoot "qqnt.json"
    $loadJs = Join-Path $NapCatRoot "loadNapCat.js"
    $mainJs = Join-Path $NapCatRoot "napcat.mjs"
    foreach ($required in @($boot, $hook, $patch, $mainJs)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "NapCat 运行文件缺失。"
        }
    }
    $mainPosix = ($mainJs -replace "\\", "/")
    $loader = "(async () => {await import(`"file:///$mainPosix`")})()"
    [System.IO.File]::WriteAllText($loadJs, $loader, $script:OutputUTF8)

    $env:NAPCAT_PATCH_PACKAGE = $patch
    $env:NAPCAT_LOAD_PATH = $loadJs
    $env:NAPCAT_INJECT_PATH = $hook
    $env:NAPCAT_LAUNCHER_PATH = $boot
    $env:NAPCAT_MAIN_PATH = $mainPosix

    $args = "`"$QQExe`" `"$hook`""
    if ($QuickLogin) {
        $args = "$args -q $BotQq"
        Write-Info "NapCat 将尝试机器人 $BotQq 快速登录；失败时请改用扫码。"
    } else {
        Write-Info "NapCat 将打开二维码。请用机器人 $BotQq 扫描，不要用主号 $OwnerQq。"
    }
    $cmd = "chcp 65001 >nul & `"$boot`" $args"
    Start-Process -FilePath "cmd.exe" -WorkingDirectory $NapCatRoot -ArgumentList @("/k", $cmd) | Out-Null
}

function Start-DashboardWindow {
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Warn "未找到 npm，跳过仪表盘。"
        return
    }
    $frontend = Join-Path $RepoRoot "frontend"
    $command = @"
Set-Location -LiteralPath '$frontend'
[Console]::Title = 'YCH dashboard'
Write-Host '仪表盘开发服务。用管理员令牌登录后查看系统页；不要在终端粘贴令牌。'
npm run dev
Read-Host '按 Enter 关闭窗口'
"@
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $frontend -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-NoExit",
        "-Command", $command
    ) | Out-Null
}

function Invoke-ObserveStart {
if ($InstallShortcut) {
    Install-ObserveShortcut
}

if ($SkipStart) {
    Write-Info "只安装快捷方式，不启动进程。"
    return
}

Write-Info "YCH 观察一键启动"
Write-Info "顺序：先 YCH 监听，再 NapCat 扫码。不会打开外发。"

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "缺少 .env。请先复制 .env.example 并填写本机非密钥说明中要求的项；不要把密钥发到聊天。"
}

$settings = Read-AllowlistedSettings
if ($settings.bot_qq -ne $BotQq) {
    Write-Warn "配置中的机器人 QQ 不是 $BotQq。请确认扫码账号。"
}
if (-not $settings.env_file_present) {
    throw "未检测到 .env。"
}
if (-not $settings.onebot_token_configured) {
    Write-Warn "OneBot token 未配置。YCH 会拒绝反向 WebSocket。请先按 .env.example 填写后再启动 NapCat。"
}
if (-not $settings.ingest_enabled) {
    Write-Warn "入站开关当前为关。本脚本不会改配置。"
}

$probeHost = Get-ProbeHost $settings.admin_host
$liveUrl = "http://${probeHost}:$($settings.admin_port)/health/live"
$readyUrl = "http://${probeHost}:$($settings.admin_port)/health/ready"
$python = Get-YchPython

$alreadyLive = Test-YchLive $liveUrl
if ($alreadyLive) {
    Write-Info "YCH 已在监听，复用现有进程。"
} else {
    Write-Info "正在新窗口启动 YCH..."
    Start-YchWindow $python
    Wait-YchLive $liveUrl
    Write-Info "YCH live 已通过。"
}

$ready = Read-Ready $readyUrl
if ($ready.status -ne "ready") {
    throw "YCH ready 未通过（数据库未就绪）。未启动 NapCat。"
}
Write-Info ("观察门控：outbound={0} reply_worker={1} max_mode={2} ingest={3}" -f @(
        $settings.outbound_enabled,
        $settings.reply_worker_enabled,
        $settings.reply_runtime_max_mode,
        $settings.ingest_enabled
    ))

$blockNapCat = $false
if ($settings.outbound_enabled -or $ready.outbound_enabled) {
    if ($settings.reply_runtime_max_mode -notin @("owner_approved", "limited_auto", "auto")) {
        Write-Fail "外发已打开，但回复上限不是 owner_approved、limited_auto 或 auto。拒绝拉起 NapCat。"
        $blockNapCat = $true
    }
}
$allowedModes = @("observe_only", "shadow", "owner_approved", "limited_auto", "auto")
if ($allowedModes -notcontains $settings.reply_runtime_max_mode) {
    Write-Fail "回复运行上限不在允许列表。拒绝拉起 NapCat。"
    $blockNapCat = $true
}
if ($settings.reply_runtime_max_mode -eq "observe_only" -and $settings.reply_worker_enabled) {
    Write-Fail "观察上限下回复 worker 已打开。拒绝拉起 NapCat。"
    $blockNapCat = $true
}
if ($settings.reply_runtime_max_mode -eq "shadow" -and ($settings.outbound_enabled -or $ready.outbound_enabled)) {
    Write-Fail "shadow 上限下外发已打开。拒绝拉起 NapCat。"
    $blockNapCat = $true
}
if ($settings.qzone_publish_enabled -or $settings.qzone_profile_collection_enabled -or $settings.owner_reports_enabled -or $settings.privacy_jobs_enabled) {
    Write-Warn "空间、汇报或隐私任务开关已打开。本脚本不会改它们，但观察联调不应依赖这些能力。"
}
if (-not $settings.onebot_token_configured) {
    $blockNapCat = $true
}

if ($SkipNapCat) {
    Write-Info "已按参数跳过 NapCat。"
} elseif ($blockNapCat) {
    Write-Fail "未启动 NapCat。"
} else {
    if (-not (Test-Path -LiteralPath $NapCatRoot -PathType Container)) {
        throw "找不到同级 NapCat 目录。"
    }
    $existingNapCat = Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue
    if ($existingNapCat) {
        Write-Info "NapCat 已在运行，跳过再次启动。"
    } else {
        $qqExe = Get-QQExePath
        if (-not $qqExe) {
            throw "找不到 QQ.exe。请先安装 QQNT，或检查卸载项注册表后重试。"
        }
        Write-Info "正在新窗口启动 NapCat（用户模式，非管理员提升）。"
        Start-NapCatWindow $qqExe
    }
}

if ($WithDashboard) {
    Start-DashboardWindow
    Write-Info "仪表盘：http://127.0.0.1:5173 （用管理员令牌登录，不要把令牌发给别人）"
}

Write-Info "YCH 健康检查： $liveUrl"
Write-Info "扫码账号必须是机器人 $BotQq。"
Write-Info "关闭 YCH 窗口可停止监听；本启动器窗口可以关掉。"
}

$script:ObserveFailed = $false
try {
    Invoke-ObserveStart
} catch {
    $script:ObserveFailed = $true
    Write-Fail $_.Exception.Message
    Write-Fail "启动失败。这个窗口会停住，方便查看原因。"
}
if (-not $SkipStart) {
    Read-Host "按 Enter 关闭此窗口"
}
if ($script:ObserveFailed) {
    exit 1
}
