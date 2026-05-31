# P11 — 可移植 Windows 发布包（轻量 + install 脚本）

> 目标：Windows 一键启动。zip 解压后 `start.bat` 自动起后端 + Edge App 模式开 webui。LibreOffice 不进 zip，由 `install.ps1` 拉取，避免 zip 过大。

---

## 1. 用户最终决策（2026-06-01）

| 决策 | 选项 |
|---|---|
| 打包范围 | **不带 LibreOffice**，提供 `install.ps1` 自动下载 LibreOffice portable 到 `runtime/libreoffice/`。zip ~200MB |
| 启动 webui | `start.bat` 优先 `msedge --app=...`（Edge App 模式） |
| 构建 | 本地脚本先做；GitHub Actions 后做 |

---

## 2. 最终发布物形态

### 2.1 zip 内容（解压后）

```
papercast-studio-0.1.0-win-x64/
├── start.bat                    # 双击启动
├── install.ps1                  # 首次运行：装 LibreOffice portable
├── README.RELEASE.md            # 用户手册（中文）
├── runtime/
│   ├── python/                  # python-build-standalone 3.11 win-x64 (~30MB)
│   ├── ffmpeg/                  # ffmpeg-release-essentials portable (~80MB)
│   │   └── bin/ffmpeg.exe
│   └── libreoffice/             # 空，由 install.ps1 填充 (~600MB)
│       └── .gitkeep
├── app/                         # papercast python 包 + 已构建的前端 dist
│   ├── papercast/
│   │   └── server/static/       # webui dist
│   ├── pyproject.toml
│   └── ...
├── prompts/                     # LLM prompt 模板
├── templates/                   # PPT 模板
│   ├── lab_template.pptx
│   └── lab_template.meta.json
├── config/
│   ├── config.yaml              # 默认配置（用户可编辑）
│   ├── secrets.env.template     # 提示填哪些 key
│   └── voices.json.template     # 空数组，首次启动复制为 voices.json
├── inbox/                       # 拖 PDF 进来
├── archive/  work/  review/  output/
└── logs/
```

zip 大小估算：
- runtime/python/: ~30MB
- runtime/ffmpeg/: ~80MB
- runtime/python/Lib/site-packages/（py-pptx + pymupdf + httpx + …）: ~80MB
- app/ + dist/: ~5MB
- 其他: ~5MB
- **合计 ~200MB**

### 2.2 LibreOffice 增量装

`install.ps1` 第一次跑：
```
1. 检查 runtime/libreoffice/program/soffice.exe，已存在 → 跳过
2. 下载 LibreOffice portable 8.x release zip（~250MB）
3. 解压到 runtime/libreoffice/
4. 验证 soffice.exe --version
```

走 SourceForge / TDF mirror：
- https://download.documentfoundation.org/libreoffice/portable/8.x.x/LibreOfficePortable_8.x.x_MultilingualStandard.paf.exe

但 .paf.exe 是 PortableApps 安装器，不是 zip。备选：
- LibreOffice 官方 sdk + portable 是 .exe self-extract，要 wrap
- **更简单**：直接用 `winget` 装到系统全局位置，PATH 全局可见。但题目要求"零门槛"…

**最终方案**：`install.ps1` 检测 winget 在 → 用 winget 装系统级；不在 → 退回手动下 portable zip 解压。最坏也只是给个明确链接。

### 2.3 start.bat 行为

```bat
@echo off
setlocal

REM 1. Set PATH to point at portable runtime (process-scoped only).
set "RUNTIME=%~dp0runtime"
set "PATH=%RUNTIME%\python;%RUNTIME%\python\Scripts;%RUNTIME%\ffmpeg\bin;%RUNTIME%\libreoffice\program;%PATH%"

REM 2. Pre-flight: complain if soffice missing.
where soffice.exe >nul 2>&1
if errorlevel 1 (
  echo [warn] LibreOffice not detected. Run install.ps1 to install it.
  echo Press any key to continue anyway, or Ctrl+C to abort.
  pause >nul
)

REM 3. Bootstrap voices.json if missing.
if not exist "%~dp0config\voices.json" (
  copy "%~dp0config\voices.json.template" "%~dp0config\voices.json" >nul
)

REM 4. Boot the server (background).
start "" /b "%RUNTIME%\python\python.exe" -m papercast.server --port 8765 --log-level warning

REM 5. Wait for /api/health to come up (5s timeout).
powershell -NoProfile -Command "$sw = [Diagnostics.Stopwatch]::StartNew(); while ($sw.ElapsedMilliseconds -lt 30000) { try { (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 1).StatusCode -eq 200; break } catch { Start-Sleep -Milliseconds 500 } }"

REM 6. Open webui — Edge --app preferred.
where msedge.exe >nul 2>&1
if not errorlevel 1 (
  start "" msedge --app=http://127.0.0.1:8765
) else (
  start "" http://127.0.0.1:8765
)

echo papercast-studio is running at http://127.0.0.1:8765
echo Close this window to stop the server.
pause
```

### 2.4 install.ps1 行为

```powershell
# install.ps1 — first-run helper

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$lo = Join-Path $here "runtime\libreoffice"
$lo_exe = Join-Path $lo "program\soffice.exe"

if (Test-Path $lo_exe) {
    Write-Host "[ok] LibreOffice already installed at $lo_exe"
    exit 0
}

Write-Host "Downloading LibreOffice portable... (~250MB, 5-10 min)"
$tmp = Join-Path $env:TEMP "libreoffice_portable.zip"
Invoke-WebRequest "https://github.com/.../libreoffice_portable_zh.zip" -OutFile $tmp
# unpacks to runtime/libreoffice/
Expand-Archive $tmp -DestinationPath $lo -Force
Remove-Item $tmp

Write-Host "[ok] LibreOffice installed at $lo_exe"
```

实际 LibreOffice portable zip 哪来？两条路：
1. **预先准备**好一份 portable zip，挂在 GitHub Release 里（我们的 release 资产之一）
2. **运行时拉取** TDF / PortableApps 上游（链接易变，且可能要 wrap PortableApps 安装器）

**推荐 #1**：构建脚本一次性下载 LibreOffice portable，重新打包成纯 zip 上传到 GitHub Release，install.ps1 从我们自己的 GH Release URL 拉。这样链接稳定，体积可控。

### 2.5 build_release.ps1 行为

构建脚本 `bootstrap/build_release.ps1`：

```
1. 检查环境
   - winget 在
   - npm 在
   - 7z (winget install 7zip.7zip)

2. 准备工作目录
   build/papercast-studio-0.1.0-win-x64/
       runtime/python/
       runtime/ffmpeg/
       runtime/libreoffice/.gitkeep
       app/
       prompts/  templates/  config/  inbox/  archive/  work/  review/  output/  logs/

3. 拉 python-build-standalone 3.11
   - https://github.com/astral-sh/python-build-standalone/releases/.../cpython-3.11.9+...x86_64-pc-windows-msvc-shared-install_only.tar.gz
   - 解压到 runtime/python/

4. 装 papercast
   - cd runtime/python
   - python.exe -m pip install --no-cache-dir <repo>[llm]
   - 这个 [llm] extra 会拉 anthropic SDK

5. 拉 ffmpeg portable
   - https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
   - 解压取 bin/ 到 runtime/ffmpeg/bin/

6. 构建 webui
   - cd webui
   - npm ci
   - npm run build  → 输出到 papercast/server/static/

7. 拷贝 app/
   - papercast/  →  build/.../app/papercast/
   - pyproject.toml, README.md  →  build/.../app/

8. 拷贝模板 + prompts + 默认 config
   - templates/, prompts/  →  build/.../
   - config.yaml.example → config/config.yaml
   - 生成 secrets.env.template, voices.json.template

9. 拷贝静态文件
   - start.bat、install.ps1、README.RELEASE.md (zh-CN)

10. 7z 压缩
    7z a -t7z -mx=9 papercast-studio-0.1.0-win-x64.zip build/papercast-studio-0.1.0-win-x64/
```

---

## 3. 实施分组

### P11.1 准备模板与默认配置文件

- `bootstrap/templates/start.bat` — 启动脚本模板
- `bootstrap/templates/install.ps1` — LibreOffice 拉取脚本
- `bootstrap/templates/README.RELEASE.md` — 给用户的使用手册
- `bootstrap/templates/config.yaml.default` — 默认 config（无密钥）
- `bootstrap/templates/secrets.env.template` — 注释化的密钥模板
- `bootstrap/templates/voices.json.template` — `[]`

### P11.2 build_release.ps1 主脚本

- 一份 PowerShell 脚本完成 §2.5 的 10 步
- 中间步骤可单独跑（`-Skip-Python -Skip-Ffmpeg` 等 flag），避免每次重头来
- 最终输出 `dist/papercast-studio-{ver}-win-x64.zip`
- 失败任何一步立即 abort，错误信息明确

### P11.3 LibreOffice 准备脚本

- `bootstrap/prepare_libreoffice.ps1`：构建机一次性运行
- 下 LibreOffice portable，重新打包成扁平 zip（不带 PortableApps 启动器）
- 输出 `dist/libreoffice-portable-{ver}-win-x64.zip`，手工 upload 到 GH Release
- install.ps1 里的 URL 指向 GH Release 资产

### P11.4 文档

- `docs/RELEASE.md` — 怎么构建、怎么 release、QA checklist
- `docs/INSTALL.md`（给最终用户）— 解压、运行 install.ps1、双击 start.bat

### P11.5 GitHub Actions（可选，本期延后）

`.github/workflows/release.yml`：
- 触发：push tag `v*`
- runs-on: windows-latest
- 步骤：复用 build_release.ps1 + prepare_libreoffice.ps1
- 上传 zip 到 release 资产

**P11.5 不在本期范围**，等本地脚本跑通且产物验证可用再加。

---

## 4. 不做（明确边界）

- **不做 lite/full 双版本** — 只做一个 zip + install.ps1 增量装 LibreOffice
- **不做 macOS / Linux 包** — 项目目标是 Windows lab；其他系统让用户走 `pip install -e .`
- **不做更新机制** — 用户重新下 zip 解压覆盖即可
- **不做卸载** — 删目录就完事
- **不做托管 LibreOffice 上游 mirror** — 只 mirror 我们 build 时下载的快照
- **不动 papercast 代码寻路逻辑** — `find_ffmpeg` / `find_soffice` 已经 PATH-first，start.bat 设 PATH 即可

---

## 5. 文件清单

**新增**：
- `bootstrap/build_release.ps1` — 主构建脚本
- `bootstrap/prepare_libreoffice.ps1` — LibreOffice portable 重打包（手工跑）
- `bootstrap/templates/start.bat`
- `bootstrap/templates/install.ps1`
- `bootstrap/templates/README.RELEASE.md`
- `bootstrap/templates/config.yaml.default`
- `bootstrap/templates/secrets.env.template`
- `bootstrap/templates/voices.json.template`
- `docs/RELEASE.md` — 给开发者
- `docs/INSTALL.md` — 给最终用户
- `docs/PLAN_P11_BUNDLE.md`（本文件）

**修改**：
- `.gitignore` —— 加 `build/`、`dist/`
- `docs/PLAN_WEBUI.md` —— P11 进度

**memory**：
- `feedback-p11-bundle.md` — 决策：不带 LO / install.ps1 / Edge --app / 本地脚本先

---

## 6. 时间盒

| 阶段 | 工作量 | 交付 |
|---|---|---|
| P11.1 模板文件 | 1h | start.bat/install.ps1/README/默认 config |
| P11.2 build_release.ps1 主脚本 | 2-3h | 能跑通的本地构建 |
| P11.3 LibreOffice 重打包脚本 | 1h | 一次性脚本 |
| P11.4 文档 | 0.5h | RELEASE.md + INSTALL.md |
| 验证（在另一台机器/VM） | 1h | smoke through full pipeline |

总计 ~5-6h。**P11.5 GitHub Actions 不在本期**。

---

## 7. 验收

- [ ] 在干净的 Windows 机器（或新装的 VM）上：
  - 解压 zip
  - 跑 `install.ps1`，LibreOffice 装入 runtime/libreoffice/
  - 双击 `start.bat`
  - Edge 自动开 http://127.0.0.1:8765
  - 上传一个测试 PDF 走完全流水线（包含 LLM 调用）
  - 视频生成在 output/
- [ ] 本地 build_release.ps1 能跑通，从源码到 zip 一气呵成
- [ ] zip 大小 ≤ 250MB
