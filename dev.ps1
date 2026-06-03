<#
.SYNOPSIS
  papercast-studio 本地开发启动脚本（后端 FastAPI + 前端 Vite）。

.DESCRIPTION
  在两个独立 PowerShell 窗口里启动：
    - 后端 FastAPI:  http://127.0.0.1:8765 （Swagger: /docs）
    - 前端 Vite:     http://127.0.0.1:5173

  脚本会先做前置检查（conda env、配置文件、node_modules），缺什么报什么。
  关闭对应窗口 = 停服务；要全停就关两个窗口。

.PARAMETER BackendOnly
  仅启动后端（用 curl / Swagger 时）

.PARAMETER FrontendOnly
  仅启动前端（后端已在别处跑）

.PARAMETER NoNewWindow
  不开新窗口，前台跑后端（前端必须 -FrontendOnly 或单独跑）

.PARAMETER CondaEnv
  conda 环境名，默认 papercast-studio

.EXAMPLE
  .\dev.ps1
  # 默认：开两个窗口同时跑前后端

.EXAMPLE
  .\dev.ps1 -BackendOnly
  # 只跑后端

.EXAMPLE
  .\dev.ps1 -FrontendOnly
  # 只跑前端
#>

[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$NoNewWindow,
    [string]$CondaEnv = 'papercast-studio'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

function Write-Info($msg)  { Write-Host "[dev] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "[dev] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[dev] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[dev] $msg" -ForegroundColor Red }

# ---------- 前置检查 ----------

function Test-CondaEnv {
    param([string]$Name)
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if (-not $conda) {
        Write-Err "找不到 conda，先装 Miniconda/Anaconda 或把 conda 加到 PATH"
        return $false
    }
    $envs = & conda env list 2>$null
    if ($envs -match "^\s*$([regex]::Escape($Name))\s") {
        return $true
    }
    Write-Err "conda env '$Name' 不存在。先按 README §首次安装 创建："
    Write-Host "    conda create -n $Name python=3.11 -y" -ForegroundColor Gray
    Write-Host "    conda activate $Name" -ForegroundColor Gray
    Write-Host "    pip install -e `".[dev,llm]`"" -ForegroundColor Gray
    return $false
}

function Test-Configs {
    $missing = @()
    if (-not (Test-Path "$RepoRoot\config\config.yaml"))   { $missing += 'config\config.yaml' }
    if (-not (Test-Path "$RepoRoot\config\secrets.env"))   { $missing += 'config\secrets.env' }
    if ($missing.Count -gt 0) {
        Write-Warn2 "缺少配置文件：$($missing -join ', ')"
        Write-Host "    cp config\config.example.yaml config\config.yaml" -ForegroundColor Gray
        Write-Host "    cp config\secrets.example.env  config\secrets.env" -ForegroundColor Gray
        Write-Warn2 "服务能起，但调用到对应阶段会报错"
    }
    if (-not (Test-Path "$RepoRoot\templates\lab_template.meta.json")) {
        Write-Warn2 "PPT 模板未解析：缺 templates\lab_template.meta.json"
        Write-Host "    conda run -n $CondaEnv papercast template-parse" -ForegroundColor Gray
    }
}

function Ensure-WebuiDeps {
    $webui = Join-Path $RepoRoot 'webui'
    if (-not (Test-Path "$webui\package.json")) {
        Write-Err "webui\package.json 不存在，仓库结构异常"
        return $false
    }
    if (-not (Test-Path "$webui\node_modules")) {
        Write-Info "webui\node_modules 不存在，跑 npm install …"
        Push-Location $webui
        try {
            npm install
            if ($LASTEXITCODE -ne 0) {
                Write-Err "npm install 失败"
                return $false
            }
        } finally {
            Pop-Location
        }
    }
    return $true
}

# ---------- 启动 ----------

function Start-Backend {
    param([switch]$Inline)

    $title  = 'papercast · backend (8765)'
    # conda run --no-capture-output 保留 stdout/stderr 流和 Ctrl+C 处理
    $cmd    = "conda run -n $CondaEnv --no-capture-output python -m papercast.server --reload --log-level info"

    if ($Inline) {
        Write-Info "启动后端（前台）：$cmd"
        Invoke-Expression $cmd
        return
    }

    Write-Info "启动后端窗口：$title"
    $psArgs = @(
        '-NoExit',
        '-Command',
        "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$RepoRoot'; Write-Host '[backend] $cmd' -ForegroundColor Cyan; $cmd"
    )
    Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -WorkingDirectory $RepoRoot | Out-Null
}

function Start-Frontend {
    $title  = 'papercast · frontend (5173)'
    $webui  = Join-Path $RepoRoot 'webui'
    $cmd    = 'npm run dev'

    Write-Info "启动前端窗口：$title"
    $psArgs = @(
        '-NoExit',
        '-Command',
        "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$webui'; Write-Host '[frontend] $cmd' -ForegroundColor Cyan; $cmd"
    )
    Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -WorkingDirectory $webui | Out-Null
}

# ---------- main ----------

Write-Info "papercast-studio 开发服务启动器"
Write-Info "仓库根目录：$RepoRoot"

$startBackend  = -not $FrontendOnly
$startFrontend = -not $BackendOnly

if ($startBackend) {
    if (-not (Test-CondaEnv -Name $CondaEnv)) { exit 1 }
    Test-Configs
}

if ($startFrontend) {
    if (-not (Ensure-WebuiDeps)) { exit 1 }
}

if ($startBackend) {
    if ($NoNewWindow -and -not $startFrontend) {
        Start-Backend -Inline
        exit 0
    } else {
        Start-Backend
    }
}

if ($startFrontend) {
    if ($NoNewWindow -and -not $startBackend) {
        $webui = Join-Path $RepoRoot 'webui'
        Push-Location $webui
        try { npm run dev } finally { Pop-Location }
        exit 0
    } else {
        Start-Frontend
    }
}

Write-Ok "已启动。打开浏览器："
if ($startFrontend) { Write-Host "    http://127.0.0.1:5173    (Web UI)" -ForegroundColor White }
if ($startBackend)  { Write-Host "    http://127.0.0.1:8765/docs  (Swagger)" -ForegroundColor White }
Write-Info "停服务：关掉对应窗口即可"
