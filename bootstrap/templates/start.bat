@echo off
REM ===========================================================================
REM PaperCast Studio · Windows portable launcher
REM
REM Sets a process-scoped PATH so the embedded Python / ffmpeg / LibreOffice
REM portable take precedence over (or fill in for) anything on the user's
REM system PATH. Then boots the FastAPI server and opens the WebUI in Edge's
REM "App mode" (window without an address bar) — falls back to the default
REM browser if Edge isn't installed.
REM ===========================================================================
setlocal enableextensions

set "ROOT=%~dp0"
set "RUNTIME=%ROOT%runtime"
set "PORT=8765"

REM ---------------------------------------------------------------------------
REM 1. Process-scoped PATH points at our bundled portables.
REM ---------------------------------------------------------------------------
set "PATH=%RUNTIME%\python;%RUNTIME%\python\Scripts;%RUNTIME%\ffmpeg\bin;%RUNTIME%\libreoffice\program;%PATH%"

REM ---------------------------------------------------------------------------
REM 2. Bootstrap: copy template configs on first run (non-destructive).
REM ---------------------------------------------------------------------------
if not exist "%ROOT%config\voices.json" (
    if exist "%ROOT%config\voices.json.template" (
        copy /y "%ROOT%config\voices.json.template" "%ROOT%config\voices.json" >nul
    )
)
if not exist "%ROOT%config\secrets.env" (
    if exist "%ROOT%config\secrets.env.template" (
        copy /y "%ROOT%config\secrets.env.template" "%ROOT%config\secrets.env" >nul
        echo [info] Created config\secrets.env from template — fill in your API keys.
    )
)

REM ---------------------------------------------------------------------------
REM 3. Soft pre-flight: warn (don't block) if LibreOffice is still missing.
REM    The full pipeline needs soffice to render PPT → PNG; the WebUI itself
REM    boots fine without it.
REM ---------------------------------------------------------------------------
where soffice.exe >nul 2>&1
if errorlevel 1 (
    echo.
    echo [warn] LibreOffice was not detected.
    echo        Run install.ps1 to download the portable build into runtime\libreoffice\.
    echo        The WebUI will start regardless, but PPT-to-video composition needs it.
    echo.
)

REM ---------------------------------------------------------------------------
REM 4. Boot the server in a child process.
REM ---------------------------------------------------------------------------
echo Starting papercast-studio at http://127.0.0.1:%PORT% ...
start "papercast-studio backend" /b "%RUNTIME%\python\python.exe" -m papercast.server --port %PORT% --log-level warning

REM ---------------------------------------------------------------------------
REM 5. Wait up to 30s for /api/health to respond, then open the browser.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$timeout = (Get-Date).AddSeconds(30); while ((Get-Date) -lt $timeout) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch { Start-Sleep -Milliseconds 500 } }; Write-Host '[warn] Server did not respond within 30 s — continuing anyway.'"

where msedge.exe >nul 2>&1
if not errorlevel 1 (
    start "" msedge --app=http://127.0.0.1:%PORT%/
) else (
    REM Fallback: shell-open the URL in whichever browser is the default.
    start "" "http://127.0.0.1:%PORT%/"
)

echo.
echo --------------------------------------------------------------------------
echo  papercast-studio is running.
echo  Close THIS WINDOW to stop the server.
echo --------------------------------------------------------------------------
echo.
pause >nul
endlocal
