# =============================================================================
# prepare_libreoffice.ps1 — OFFLINE/AIR-GAPPED helper (rarely needed)
#
# When `install.ps1 -Mode Portable` is selected, it pulls a pre-built
# portable zip from this repo's GitHub release assets. This script is
# how we BUILD that asset for upload. Most users / contributors don't
# need to run it.
#
# Why we ship it anyway:
#   - Lab networks behind firewalls / no winget access can rebuild the
#     mirror on their own infrastructure
#   - Reproducibility: the zip we host is documented to come from a
#     specific upstream version
#
# What it does:
#   1. Downloads the LibreOffice MSI from documentfoundation.org
#   2. Extracts via msiexec /a (administrative install — stages files
#      without running InstallShield UI)
#   3. Strips localizations + help files we don't ship (drops ~150MB)
#   4. Re-zips into dist\libreoffice-portable-win-x64.zip
#
# After this script: upload the zip manually to:
#   https://github.com/Garfield-Wuu/papercast-studio/releases
#
# Usage:
#   .\bootstrap\prepare_libreoffice.ps1
#   .\bootstrap\prepare_libreoffice.ps1 -Version 25.2.5.2
# =============================================================================

[CmdletBinding()]
param(
    [string]$Version = "25.2.5.2",
    [switch]$KeepLocalizations
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Cache = Join-Path $Repo "build\.cache"
$Dist = Join-Path $Repo "dist"
$Stage = Join-Path $Repo "build\libreoffice-stage"

New-Item -ItemType Directory -Path $Cache -Force | Out-Null
New-Item -ItemType Directory -Path $Dist -Force | Out-Null

$Url = "https://download.documentfoundation.org/libreoffice/stable/$Version/win/x86_64/LibreOffice_${Version}_Win_x86-64.msi"
$Msi = Join-Path $Cache "LibreOffice_${Version}_Win_x86-64.msi"

if (-not (Test-Path $Msi)) {
    Write-Host "Downloading LibreOffice $Version (~340MB) ..." -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Msi -UseBasicParsing
}

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null

Write-Host "Running msiexec administrative install (stages files only) ..." -ForegroundColor Yellow
$logPath = Join-Path $Cache "msi-extract.log"
$proc = Start-Process -FilePath "msiexec.exe" `
    -ArgumentList @("/a", "`"$Msi`"", "/qb", "TARGETDIR=`"$Stage`"", "/L*v", "`"$logPath`"") `
    -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Host "[err] msiexec exit=$($proc.ExitCode); see $logPath" -ForegroundColor Red
    exit 1
}

# msiexec /a creates a "LibreOffice" subdir; we want its contents at the root.
$inner = Join-Path $Stage "LibreOffice"
if (-not (Test-Path $inner)) {
    throw "Unexpected MSI extract layout under $Stage"
}

# Strip locale files unless told otherwise; English UI is enough for our use.
if (-not $KeepLocalizations) {
    Write-Host "Stripping non-en-US localizations + help files ..." -ForegroundColor Yellow
    $strip = @(
        "share\config\soffice.cfg\modules\swriter\images_*\images.zip",
        "readmes",
        "share\extensions\dict-*",
        "help\*",
        "presets",
        "share\autotext\*"
    )
    foreach ($pat in $strip) {
        Get-ChildItem -Path $inner -Recurse -Filter $pat -ErrorAction SilentlyContinue `
            | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Remove the staged MSI itself (msiexec /a leaves it behind).
Remove-Item (Join-Path $Stage "*.msi") -Force -ErrorAction SilentlyContinue

# Sanity.
$soffice = Join-Path $inner "program\soffice.exe"
if (-not (Test-Path $soffice)) {
    throw "After staging, $soffice still not found"
}

# -----------------------------------------------------------------------------
# Repack as flat zip (so install.ps1's Expand-Archive lands soffice at
# runtime\libreoffice\program\soffice.exe directly).
# -----------------------------------------------------------------------------
$ZipName = "libreoffice-portable-win-x64.zip"
$ZipPath = Join-Path $Dist $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "Compressing ..." -ForegroundColor Yellow
$sevenZip = Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($sevenZip) {
    Push-Location $inner
    try {
        & 7z a -tzip -mx=7 -y $ZipPath . | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "7z packaging failed" }
    } finally {
        Pop-Location
    }
} else {
    Compress-Archive -Path (Join-Path $inner "*") -DestinationPath $ZipPath -CompressionLevel Optimal -Force
}

$size = (Get-Item $ZipPath).Length / 1MB
Write-Host ""
Write-Host "[done] $ZipPath  ($([math]::Round($size, 1)) MB)" -ForegroundColor Green
Write-Host "Upload manually to:"
Write-Host "  https://github.com/Garfield-Wuu/papercast-studio/releases" -ForegroundColor Cyan
