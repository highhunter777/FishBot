param(
    [switch]$Force
)

$ErrorActionPreference = 'SilentlyContinue'
$root = 'E:\FishBot'
$url = 'http://127.0.0.1:8010/healthz'
$log = Join-Path $root 'data\supervise.log'
$statePath = Join-Path $root 'data\supervise.json'
$python = 'C:\Python313\python.exe'

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
}

function Get-State {
    if (Test-Path $statePath) {
        try {
            $raw = Get-Content $statePath -Raw -Encoding utf8
            if ($raw) { return $raw | ConvertFrom-Json }
        } catch { }
    }
    return [pscustomobject]@{ fails = 0; last_restart = 0 }
}

function Save-State($state) {
    $state | ConvertTo-Json | Set-Content -Path $statePath -Encoding utf8
}

function Send-Notice([string]$title, [string]$message) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\notify.ps1') -Title $title -Message $message
}

function Test-Health {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 8
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

$state = Get-State

# 健康就直接退出（-Force 也一样）：登录触发时服务若已在跑，不该被白重启一次
if (Test-Health) {
    if ([int]$state.fails -ne 0) {
        $state.fails = 0
        Save-State $state
    }
    exit 0
}

$state.fails = [int]$state.fails + 1
Write-Log "健康检查失败（连续 $($state.fails) 次）"

$now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
$last = [int64]$state.last_restart

# 连续两次失败才动手，避免瞬时抖动
if (-not $Force -and [int]$state.fails -lt 2) {
    Save-State $state
    exit 1
}

# 10 分钟内重启过就再等等，避免反复重启
if (-not $Force -and ($now - $last) -lt 600) {
    Write-Log '距上次重启不足 10 分钟，跳过'
    Save-State $state
    exit 1
}

Write-Log '开始重启 FishBot'
Send-Notice 'FishBot 无响应' '检测到服务挂了，正在自动重启'

$listeners = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue
foreach ($item in $listeners) {
    $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq 'python') {
        Stop-Process -Id $proc.Id -Force
        Write-Log "已结束进程 $($proc.Id)"
    }
}
Start-Sleep -Seconds 2

Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8010' `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root 'data\uvicorn.out.log') `
    -RedirectStandardError (Join-Path $root 'data\uvicorn.err.log')

$state.last_restart = $now
$state.fails = 0
Save-State $state

Start-Sleep -Seconds 15

if (Test-Health) {
    Write-Log '重启成功'
    Send-Notice 'FishBot 已恢复' '服务重启成功，可以继续用了'
    exit 0
}

Write-Log '重启后仍不可用'
Send-Notice 'FishBot 重启失败' '自动重启后仍然无响应，需要人工看一下'
exit 2
