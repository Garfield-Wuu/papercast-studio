@echo off
REM 双击启动 papercast-studio 本地开发服务（前后端各一个 PowerShell 窗口）
REM 等价于：powershell -ExecutionPolicy Bypass -File dev.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
