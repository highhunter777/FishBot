param(
    [string]$Title = "FishBot",
    [string]$Message = ""
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Warning
$notify.BalloonTipTitle = $Title
$notify.BalloonTipText = $Message
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Warning
$notify.Visible = $true

# 给系统一点时间把气泡挂上去
Start-Sleep -Milliseconds 300
$notify.ShowBalloonTip(10000)
Start-Sleep -Seconds 11
$notify.Visible = $false
$notify.Dispose()
