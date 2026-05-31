# Release process

How to cut a Windows portable release of papercast-studio.

## TL;DR

```powershell
# from the repo root, in PowerShell
.\bootstrap\build_release.ps1

# output:
#   build\papercast-studio-{version}-win-x64\        ← staged tree
#   dist\papercast-studio-{version}-win-x64.zip      ← upload this
```

## Prereqs (build machine)

- Windows 10 / 11 with PowerShell 5.1+
- Node.js 20+ + npm 10+ (for `webui/`)
- Python 3.11+ on PATH (only used to read pyproject metadata; the
  bundled interpreter is downloaded fresh every build)
- Git
- Optional: 7-Zip (faster zip step). Without it the script falls back
  to PowerShell's `Compress-Archive`.

## What `build_release.ps1` does

1. Reads version from `pyproject.toml`
2. Wipes `build\papercast-studio-{ver}-win-x64\`
3. Pulls **python-build-standalone** CPython 3.11 (cached in
   `build\.cache\`) → `runtime\python\`
4. Runs `python.exe -m pip install --no-cache-dir <repo>[llm]` against
   the embedded interpreter
5. Pulls **ffmpeg-release-essentials** zip → `runtime\ffmpeg\bin\`
6. Builds the webui (`npm ci && npm run build`); vite output lands in
   `papercast/server/static/`
7. Copies `prompts/`, `templates/`, default config, `start.bat`,
   `install.ps1`, `README.md`
8. Strips `__pycache__` + `*.pyc` from the embedded site-packages to
   shave size
9. Compresses to `dist\papercast-studio-{ver}-win-x64.zip`

`-SkipPython` / `-SkipFfmpeg` / `-SkipWebui` / `-SkipZip` flags let
you reuse cached pieces during iteration.

## What `prepare_libreoffice.ps1` does

Builds the **portable LibreOffice zip** that `install.ps1 -Mode
Portable` downloads. You only need this if you want to refresh the
asset on GitHub Releases — the install script defaults to `winget`,
which doesn't need our mirror.

```powershell
.\bootstrap\prepare_libreoffice.ps1 -Version 25.2.5.2
# output: dist\libreoffice-portable-win-x64.zip
# upload manually to GH releases
```

## QA checklist before tagging a release

On a clean Windows VM (or a teammate's machine):

1. Extract the zip to `D:\papercast-studio\` (no spaces / OneDrive)
2. Right-click `install.ps1` → Run with PowerShell. Should finish
   without prompting for paths
3. Edit `config\secrets.env` — fill in real `ANTHROPIC_API_KEY` +
   `MINIMAX_API_KEY`
4. Double-click `start.bat`
   - Edge App window opens at `http://127.0.0.1:8765/`
   - 配置 page shows 5 / 5 dependencies green
5. Drag a known-good test PDF into the upload area; fill the dialog
6. Wait for the pipeline to reach **awaiting_review**; open the review
   panel; click 全部通过
7. Verify mp4 appears under `output\`
8. Close the start.bat window — server stops cleanly
9. Reopen → 工作区 should still show the previous task (DB intact)

If any step fails, do NOT push the tag. Open an issue with the
failing step + the last few lines of the start.bat console.

## Tagging + uploading

```bash
git tag -a v0.1.0 -m "release: 0.1.0"
git push origin v0.1.0
```

Then on github.com → Releases → Draft a new release → pick the tag →
upload `dist\papercast-studio-0.1.0-win-x64.zip` (and optionally the
LibreOffice portable mirror zip).

A future GitHub Actions workflow (post-P11) will automate this.

## Versioning

Bump `pyproject.toml`'s `version` field; the build script picks it up
automatically. Keep semver-ish: `0.x.y` while the WebUI surface is
still being shaken out.
