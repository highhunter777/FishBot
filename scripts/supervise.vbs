' 计划任务用：静默运行 supervise.ps1，不闪控制台窗口
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\FishBot\scripts\supervise.ps1""", 0, False
