# =============================================================================
# build_release.ps1 — package papercast-studio for Windows distribution.
#
# Output:
#   dist\papercast-studio-{version}-win-x64.zip
#
# Composes:
#   - python-build-standalone CPython 3.11 (~30MB) into runtime\python\
#   - Gyan ffmpeg-release-essentials (~80MB) into runtime\ffmpeg\
#   - The papercast Python package + [llm] extras into runtime\python\
#   - The webui frontend, pre-built into papercast\server\static\
#   - bootstrap/templates/* renamed/copied to release-friendly names
#
# LibreOffice is NOT included — install.ps1 fetches it on first run.
# See bootstrap/prepare_libreoffice.ps1 for how the LibreOffice asset
# itself is built (one-off, uploaded to GitHub Releases manually).
#
# Usage:
#   .\bootstrap\build_release.ps1                 # full build
#   .\bootstrap\build_release.ps1 -SkipPython     # reuse cached python
#   .\bootstrap\build_release.ps1 -SkipFfmpeg     # reuse cached ffmpeg
#   .\bootstrap\build_release.ps1 -SkipWebui      # reuse cached dist
#   .\bootstrap\build_release.ps1 -SkipZip        # produce build/ but no zip
#
# Prereqs on the build machine: PowerShell 5.1+, npm 9+, 7zip on PATH OR
# Compress-Archive (built-in) — we fall back to Compress-Archive when 7z
# is missing.
# =============================================================================

[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$SkipFfmpeg,
    [switch]$SkipWebui,
    [switch]$SkipZip,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # speeds up Invoke-WebRequest

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Build = Join-Path $Repo "build"
$Cache = Join-Path $Repo "build\.cache"
$Dist = Join-Path $Repo "dist"
$Templates = Join-Path $Repo "bootstrap\templates"

# -----------------------------------------------------------------------------
# Resolve version (from pyproject.toml unless overridden).
# -----------------------------------------------------------------------------
if (-not $Version) {
    $pyproject = Get-Content (Join-Path $Repo "pyproject.toml") -Raw
    if ($pyproject -match 'version\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "Could not parse version from pyproject.toml"
    }
}

$ReleaseName = "papercast-studio-$Version-win-x64"
$Stage = Join-Path $Build $ReleaseName

Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  Building $ReleaseName"
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "Repo : $Repo"
Write-Host "Stage: $Stage"
Write-Host "Cache: $Cache"
Write-Host "Dist : $Dist"
Write-Host ""

# -----------------------------------------------------------------------------
# URLs — pinned to known-good versions. Bump deliberately.
# -----------------------------------------------------------------------------
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20251104/cpython-3.11.13+20251104-x86_64-pc-windows-msvc-install_only.tar.gz"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
function Ensure-Dir($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

function Download-Cached($url, $dest) {
    if (Test-Path $dest) {
        Write-Host "[cache] $dest" -ForegroundColor DarkGray
        return
    }
    Write-Host "[get  ] $url" -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

function Extract-TarGz($archive, $dest) {
    # Windows 10 / 11 ship with `tar` from BSD libarchive — handles tar.gz fine.
    Ensure-Dir $dest
    & tar -xzf $archive -C $dest
    if ($LASTEXITCODE -ne 0) { throw "tar -xzf $archive failed" }
}

function Extract-Zip($archive, $dest) {
    Ensure-Dir $dest
    Expand-Archive -Path $archive -DestinationPath $dest -Force
}

# -----------------------------------------------------------------------------
# 1. Stage directories (wipe & recreate).
# -----------------------------------------------------------------------------
Write-Host "[step ] preparing stage directory ..." -ForegroundColor Cyan
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
Ensure-Dir $Stage
Ensure-Dir $Cache
Ensure-Dir $Dist
foreach ($d in @(
    "runtime\python", "runtime\ffmpeg", "runtime\libreoffice",
    "config", "templates", "prompts",
    "inbox", "archive", "work", "review", "output", "logs"
)) {
    Ensure-Dir (Join-Path $Stage $d)
}

# Empty ".gitkeep"s so the runtime\libreoffice\ folder is preserved by zip.
"This directory is filled by install.ps1 (downloads LibreOffice portable)." `
    | Set-Content (Join-Path $Stage "runtime\libreoffice\README.txt")

# P11.2_NEXT

# -----------------------------------------------------------------------------
# 2. Embed Python (python-build-standalone).
# -----------------------------------------------------------------------------
$PythonStage = Join-Path $Stage "runtime\python"
if (-not $SkipPython) {
    Write-Host "[step ] fetching CPython 3.11 (python-build-standalone) ..." -ForegroundColor Cyan
    $pyArchive = Join-Path $Cache "cpython-win-x64.tar.gz"
    Download-Cached $PythonUrl $pyArchive

    $pyExtract = Join-Path $Cache "python-extract"
    if (Test-Path $pyExtract) { Remove-Item $pyExtract -Recurse -Force }
    Extract-TarGz $pyArchive $pyExtract

    # Archive layout: python-extract\python\ → flatten into runtime\python\
    $inner = Join-Path $pyExtract "python"
    if (-not (Test-Path $inner)) {
        throw "Unexpected python-build-standalone archive layout: $pyExtract"
    }
    Get-ChildItem $inner -Force | Copy-Item -Destination $PythonStage -Recurse -Force
}
$PythonExe = Join-Path $PythonStage "python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Embedded python.exe not found at $PythonExe (try without -SkipPython)"
}

# -----------------------------------------------------------------------------
# 3. pip-install papercast (with [llm]) into the embedded interpreter.
# -----------------------------------------------------------------------------
Write-Host "[step ] installing papercast into embedded Python ..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pip 2>&1 | Out-Null
& $PythonExe -m pip install --no-cache-dir "$Repo[llm]"
if ($LASTEXITCODE -ne 0) { throw "pip install papercast failed" }

# Sanity-check: import papercast in the embedded environment.
& $PythonExe -c "import papercast; print('papercast', papercast.__version__)"
if ($LASTEXITCODE -ne 0) { throw "Embedded python cannot import papercast" }

# -----------------------------------------------------------------------------
# 4. Pull ffmpeg portable.
# -----------------------------------------------------------------------------
$FfmpegStage = Join-Path $Stage "runtime\ffmpeg"
if (-not $SkipFfmpeg) {
    Write-Host "[step ] fetching ffmpeg-release-essentials ..." -ForegroundColor Cyan
    $ffmpegArchive = Join-Path $Cache "ffmpeg.zip"
    Download-Cached $FfmpegUrl $ffmpegArchive

    $ffExtract = Join-Path $Cache "ffmpeg-extract"
    if (Test-Path $ffExtract) { Remove-Item $ffExtract -Recurse -Force }
    Extract-Zip $ffmpegArchive $ffExtract

    # Archive layout: ffmpeg-extract\ffmpeg-x.y.z-essentials_build\bin\
    $inner = Get-ChildItem $ffExtract -Directory | Where-Object { $_.Name -like "ffmpeg-*" } | Select-Object -First 1
    if (-not $inner) { throw "Unexpected ffmpeg archive layout under $ffExtract" }
    Copy-Item -Path (Join-Path $inner.FullName "bin") -Destination (Join-Path $FfmpegStage "bin") -Recurse -Force
    # Optional: ship LICENSE for redistribution compliance.
    Copy-Item -Path (Join-Path $inner.FullName "LICENSE.txt") -Destination $FfmpegStage -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path (Join-Path $FfmpegStage "bin\ffmpeg.exe"))) {
    throw "ffmpeg.exe missing under $FfmpegStage (try without -SkipFfmpeg)"
}

# -----------------------------------------------------------------------------
# 5. Build the webui frontend (vite output → papercast/server/static).
# -----------------------------------------------------------------------------
if (-not $SkipWebui) {
    Write-Host "[step ] building webui ..." -ForegroundColor Cyan
    Push-Location (Join-Path $Repo "webui")
    try {
        if (-not (Test-Path "node_modules")) {
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally {
        Pop-Location
    }
}
$staticDir = Join-Path $Repo "papercast\server\static"
if (-not (Test-Path (Join-Path $staticDir "index.html"))) {
    throw "webui dist missing at $staticDir (run without -SkipWebui first)"
}

# -----------------------------------------------------------------------------
# 6. Copy app-side payload — the bundle's entry-point Python tree, prompts,
#    PPT templates. The pip install in step 3 already deployed papercast
#    into the embedded Python's site-packages, so we don't need a second
#    copy of papercast/ here.
# -----------------------------------------------------------------------------
Write-Host "[step ] copying app payload (prompts, templates, configs) ..." -ForegroundColor Cyan

Copy-Item -Path (Join-Path $Repo "prompts\*") -Destination (Join-Path $Stage "prompts") -Recurse -Force
Copy-Item -Path (Join-Path $Repo "templates\*") -Destination (Join-Path $Stage "templates") -Recurse -Force

# Default user-facing configs.
Copy-Item -Path (Join-Path $Templates "config.yaml.default") `
          -Destination (Join-Path $Stage "config\config.yaml") -Force
Copy-Item -Path (Join-Path $Templates "secrets.env.template") `
          -Destination (Join-Path $Stage "config\secrets.env.template") -Force
Copy-Item -Path (Join-Path $Templates "voices.json.template") `
          -Destination (Join-Path $Stage "config\voices.json.template") -Force

# Top-level launchers + readme.
Copy-Item -Path (Join-Path $Templates "start.bat") -Destination $Stage -Force
Copy-Item -Path (Join-Path $Templates "install.ps1") -Destination $Stage -Force
Copy-Item -Path (Join-Path $Templates "README.RELEASE.md") `
          -Destination (Join-Path $Stage "README.md") -Force

# -----------------------------------------------------------------------------
# 7. Strip cache + tests from the embedded site-packages to shave size.
# -----------------------------------------------------------------------------
Write-Host "[step ] stripping cache from embedded site-packages ..." -ForegroundColor Cyan
Get-ChildItem -Path $PythonStage -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue `
    | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $PythonStage -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue `
    | Remove-Item -Force -ErrorAction SilentlyContinue

# -----------------------------------------------------------------------------
# 8. Compose the zip.
# -----------------------------------------------------------------------------
if (-not $SkipZip) {
    $zipName = "$ReleaseName.zip"
    $zipPath = Join-Path $Dist $zipName
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    Write-Host "[step ] packaging $zipName ..." -ForegroundColor Cyan
    $sevenZip = (Get-Command 7z.exe -ErrorAction SilentlyContinue)
    if ($sevenZip) {
        Push-Location $Build
        try {
            & 7z a -tzip -mx=7 -y $zipPath $ReleaseName | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "7z packaging failed" }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "[info ] 7z not on PATH, falling back to Compress-Archive (slower)" -ForegroundColor DarkYellow
        Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
    }

    $size = (Get-Item $zipPath).Length / 1MB
    Write-Host ""
    Write-Host "[done ] $zipPath  ($([math]::Round($size, 1)) MB)" -ForegroundColor Green
} else {
    Write-Host "[skip ] zip step skipped; staged at $Stage" -ForegroundColor DarkYellow
}
