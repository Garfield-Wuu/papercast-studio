# =============================================================================
# install.ps1 — first-run helper for papercast-studio Windows release
#
# Pipeline reaches into LibreOffice for PowerPoint → PNG rendering
# (composer/render.py). This script puts a working soffice.exe on the
# search path. Two strategies, in order of preference:
#
#   1. winget install — system-wide, ~5 min, integrates with Windows
#      update; what we recommend for most users.
#   2. Portable fallback — only when winget is missing (Windows
#      stripped-down editions, ancient Win10). Downloads our pinned
#      LibreOffice portable zip from the GitHub release assets and
#      drops it into runtime\libreoffice\.
#
# The bundled start.bat checks runtime\libreoffice\program first, then
# falls back to whatever's on PATH (winget installs LibreOffice into
# C:\Program Files\LibreOffice, which find_soffice() picks up via its
# fallback list — so winget-style installs work without any PATH
# trickery).
#
# Usage:
#   .\install.ps1                    # try winget first
#   .\install.ps1 -Mode Portable     # force the portable path
#   .\install.ps1 -Force             # reinstall even if already present
# =============================================================================

[CmdletBinding()]
param(
    [ValidateSet("Auto", "Winget", "Portable")]
    [string]$Mode = "Auto",
    [string]$PortableUrl = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibreOfficeDir = Join-Path $Here "runtime\libreoffice"
$LocalSoffice = Join-Path $LibreOfficeDir "program\soffice.exe"
$DefaultPortableUrl = "https://github.com/Garfield-Wuu/papercast-studio/releases/latest/download/libreoffice-portable-win-x64.zip"

# -----------------------------------------------------------------------------
# Idempotency: skip if soffice already reachable somewhere we expect.
# -----------------------------------------------------------------------------
function Test-SofficeReachable {
    if (Test-Path $LocalSoffice) { return $true }
    $globalPaths = @(
        "C:\Program Files\LibreOffice\program\soffice.exe",
        "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
    )
    foreach ($p in $globalPaths) { if (Test-Path $p) { return $true } }
    return ($null -ne (Get-Command soffice.exe -ErrorAction SilentlyContinue))
}

if ((Test-SofficeReachable) -and -not $Force) {
    Write-Host "[ok] LibreOffice is already installed and reachable." -ForegroundColor Green
    Write-Host "     Run with -Force to reinstall via the portable path."
    exit 0
}

Write-Host ""
Write-Host "papercast-studio · LibreOffice installer" -ForegroundColor Cyan
Write-Host ""

# -----------------------------------------------------------------------------
# Mode resolution.
# -----------------------------------------------------------------------------
$wingetAvailable = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
$resolvedMode = $Mode
if ($resolvedMode -eq "Auto") {
    if ($wingetAvailable) { $resolvedMode = "Winget" } else { $resolvedMode = "Portable" }
}

# -----------------------------------------------------------------------------
# Branch 1: winget (preferred).
# -----------------------------------------------------------------------------
if ($resolvedMode -eq "Winget") {
    if (-not $wingetAvailable) {
        Write-Host "[err] winget is not on PATH but Mode=Winget was requested." -ForegroundColor Red
        Write-Host "      Install App Installer from the Microsoft Store, or rerun with -Mode Portable."
        exit 1
    }
    Write-Host "Using winget to install TheDocumentFoundation.LibreOffice ..." -ForegroundColor Yellow
    Write-Host "(This is system-wide. UAC will prompt for admin.)" -ForegroundColor DarkGray
    & winget install --id TheDocumentFoundation.LibreOffice --silent `
        --accept-package-agreements --accept-source-agreements
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host "[warn] winget exit=$rc — installation may have been cancelled or already current." -ForegroundColor Yellow
    }
    if (Test-SofficeReachable) {
        Write-Host "[ok] LibreOffice installed (winget). Double-click start.bat to launch." -ForegroundColor Green
        exit 0
    }
    Write-Host "[err] winget install reported success but soffice.exe not found." -ForegroundColor Red
    Write-Host "      Try logging out and back in, or rerun with -Mode Portable."
    exit 1
}

# -----------------------------------------------------------------------------
# Branch 2: portable zip (no admin needed).
# -----------------------------------------------------------------------------
$Url = $PortableUrl
if (-not $Url) { $Url = $DefaultPortableUrl }
Write-Host "Using portable installation. URL: $Url" -ForegroundColor Yellow

$TempZip = Join-Path $env:TEMP ("libreoffice-portable-" + [guid]::NewGuid() + ".zip")
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    Write-Host "Downloading (~250MB; 5-10 min on slow connections) ..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $Url -OutFile $TempZip -UseBasicParsing

    $size = (Get-Item $TempZip).Length
    if ($size -lt 50MB) {
        throw "Downloaded archive is suspiciously small ($([math]::Round($size / 1MB, 1)) MB) — try again."
    }

    if (Test-Path $LibreOfficeDir) {
        Get-ChildItem $LibreOfficeDir -Force | Where-Object { $_.Name -ne "README.txt" } `
            | Remove-Item -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path $LibreOfficeDir -Force | Out-Null
    }

    Write-Host "Extracting ..." -ForegroundColor Yellow
    Expand-Archive -Path $TempZip -DestinationPath $LibreOfficeDir -Force

    if (-not (Test-Path $LocalSoffice)) {
        # Try to flatten a single nested folder (some PortableApps layouts).
        $inner = Get-ChildItem $LibreOfficeDir -Directory `
            | Where-Object { Test-Path (Join-Path $_.FullName "program\soffice.exe") } `
            | Select-Object -First 1
        if ($inner) {
            Write-Host "[info] Flattening nested archive layout ..."
            Get-ChildItem $inner.FullName -Force | Move-Item -Destination $LibreOfficeDir -Force
            Remove-Item $inner.FullName -Force
        }
    }

    if (-not (Test-Path $LocalSoffice)) {
        throw "Archive extracted, but $LocalSoffice not found."
    }
}
finally {
    if (Test-Path $TempZip) {
        Remove-Item $TempZip -Force -ErrorAction SilentlyContinue
    }
}

# -----------------------------------------------------------------------------
# Smoke-check.
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "Verifying ..." -ForegroundColor Yellow
& $LocalSoffice --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[warn] $LocalSoffice exists but --version failed; double-click start.bat anyway." -ForegroundColor Yellow
} else {
    Write-Host "[ok] LibreOffice (portable) ready at $LocalSoffice" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Double-click start.bat to launch papercast-studio." -ForegroundColor Cyan
