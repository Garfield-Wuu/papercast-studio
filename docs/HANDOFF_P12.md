# P12 进度交接（下一会话从这里开始）

> 截至 commit `5a6356c`：P11 Windows 打包脚本写好但**未跑通**。下一会话先验证打包，然后视情况做 P12 体验打磨。

---

## 1. 当前位置

- **Branch**: main，clean working tree
- **HEAD**: `5a6356c feat(release): P11 Windows 可移植打包脚本`
- **Tests**: 356 passed, 28 skipped
- **Frontend**: tsc 0 错；vite build 通过
- **服务上次启动**: 已停（end of session 用户没跑）

---

## 2. 已完成总览（P0-P11 全部 ✅）

| 阶段 | 内容 | commit |
|---|---|---|
| P1 | LLM 接入，CLI 全自动跑通 | （早期） |
| P2 | FastAPI 后端 + REST + WebSocket + jobs | （早期） |
| P3 | 合并到 P2 | — |
| P4 | webui 工程 + 设计 token + 路由 | `a66a5e4` |
| P5 | 5-tab 审阅面板 + Monaco + 局部重生 | `7dc04ef` `c93dfd8` |
| P6 | Files / Voices / Settings 三页 | `a4c4e93` |
| P7 | 详情页重排 / Files 收紧 / 启动前填封面 / 事件流持久化 / 删 Discord / 50MB 上限 | `38e1fd3` |
| P7.x | Files PPT 图标 / 汇报日期 / vision LLM 角色占位 | `e696b1d` |
| P8 | 音色页 3 步克隆向导 + LLM 写学术汇报样本 + 在线录音 | `f55a1db` |
| P8.fix | 试听 body 流复读 / cwd 正常化 | `71e777c` |
| P9.1 | 评测脚本 + cluster 单测骨架 | `686d657` |
| P9.2-3 | 视觉簇切图（Method D） | `b46d485` |
| P9.4-7 | navbar 改名 + 总览条 + 删 vision 占位 | `a14cce3` |
| P10 | UX 二期：审阅页提顶层 / 进度 5 段 / TTS 视频 select / 音色收藏 | `42c4357` |
| P10.fix | 隐去「展开完整 12 阶段」+ 克隆向导步骤去重编号 | `ecb60c1` |
| P11 | Windows 打包脚本（**未跑通验证**） | `5a6356c` |

---

## 3. P11 打包：**未验证**，下一会话首要任务

### 3.1 已写好的文件

```
bootstrap/
├── build_release.ps1            # 主构建脚本 ~200 行
├── prepare_libreoffice.ps1      # 可选辅助（离线场景）
└── templates/
    ├── start.bat                # Edge --app 模式开 webui
    ├── install.ps1              # winget 优先 / portable fallback 装 LibreOffice
    ├── README.RELEASE.md        # 中文用户手册
    ├── config.yaml.default
    ├── secrets.env.template
    └── voices.json.template

docs/
├── PLAN_P11_BUNDLE.md           # 本期规划
└── RELEASE.md                   # 开发者 release 流程 + QA checklist
```

### 3.2 下一会话的第一件事 — 跑构建

```powershell
# 在 papercast-studio 仓库根目录
.\bootstrap\build_release.ps1
```

**预期**：~10 分钟，下载 ~120MB（CPython + ffmpeg），输出 `dist\papercast-studio-0.1.0-win-x64.zip` (~200MB)

**可能的坑（下次先排查）**：

1. **python-build-standalone URL 过期**：写死了 `20251104` 这个 release tag。如果 404，去 https://github.com/astral-sh/python-build-standalone/releases 找最新的 win-x64 install_only.tar.gz 替换
2. **Gyan ffmpeg URL**：`https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip` 是 latest 链接，应该稳定但偶尔 503
3. **`tar -xzf` 兼容性**：Win10 1803+ 内置 BSD tar 支持 .tar.gz；老系统可能要装 git-bash 或 7z
4. **`pip install <repo>[llm]` 走嵌入式 Python**：要构建机有 git（pip 拉本地仓库时偶尔需要）；离线机可能要先 `pip install -e .`
5. **npm ci 慢**：webui/node_modules/ 已存在就跳；删掉重装 ~5 分钟

### 3.3 跑通后的验证步骤

按 `docs/RELEASE.md` §QA checklist 走一遍：

1. 解压 zip 到 `D:\some-test-dir\`（不要 OneDrive、不带空格）
2. 跑 `install.ps1`（优先 winget 装 LibreOffice）
3. 编辑 `config\secrets.env` 填真实 API key（或者从已有的 main 仓库 config/ 拷过来）
4. 双击 `start.bat`，Edge App 应自动打开 http://127.0.0.1:8765
5. 上传一份测试 PDF 跑完整流程
6. 视频应该出现在 `output\YYYY-MM-DD_<pid>.mp4`

---

## 4. P12 待启动 — 体验打磨

`docs/PLAN_WEBUI.md` 里 P12 行：

> 视觉细节 / a11y / 错误兜底 / 引导文案；e2e 测试  
> UI 通过 ui-ux-pro-max checklist；Lighthouse > 90  
> 1d  🟡 待开始

**等 P11 验证通过再启动**。等 P11 跑通后用户可能在 e2e 验证里又发现一些细节，那批反馈正好就是 P12 的输入。

---

## 5. 关键架构记忆（下一会话不必重读源码就能继续）

### 5.1 流水线

```
PDF (inbox/) → ingested → parsed → figures_split → read_done → 
slides_done → script_done → awaiting_review → approved → 
tts_submitted → tts_done → composed → published (output/.mp4)
```

12 阶段写在 `papercast/core/state.py:Stage` enum；前端按 `STAGE_GROUPS` 分成 5 段大阶段（上传/解析/制作/审阅/发布）显示。

### 5.2 切图（P9）

`cfg.slides.figure_extractor: visual_cluster` 是默认；`text_blocks` 是 fallback。`papercast/reader/_clusters.py` 实现 caption + 视觉簇评分。

### 5.3 Voice 收藏（P10）

`config/voices.json` 既存克隆音色（`source: "cloned"`）也存系统音色收藏（`source: "system"`）；`is_favorite=True` 才进配置页 TTS 下拉。**这个文件已加 .gitignore**。

### 5.4 配置文件

- `config/config.yaml` — 公开配置，可入 git ✓ ❌（已 ignore）
- `config/secrets.env` — API keys，**绝不入 git**
- `config/voices.json` — 用户音色收藏，**已加入 .gitignore**

WebUI 配置页的「保存」按钮往这两个文件写。

### 5.5 服务启动

**永远从仓库根**起 server，不要在 webui/ 起：

```powershell
cd E:/projects/papercast-studio
D:/ana/envs/papercast-studio/python.exe -m papercast.server --port 8765 --log-level info
```

`__main__.py` 有 `_normalize_cwd()` 自动找 `config/config.yaml` 向上 4 层，但不要依赖它。

---

## 6. Memory 文件（下次会话会自动加载 MEMORY.md）

C:\Users\96204\.claude\projects\E--projects-papercast-studio\memory\ 已沉淀的 feedback：

- `feedback-p7-revisions.md`
- `feedback-voice-wizard.md` (P8)
- `feedback-figures-method-d.md` (P9)
- `feedback-p10-ux-iteration.md` (P10)
- `feedback-p11-bundle.md` (P11，**最新**)

---

## 7. 启动下一会话的命令

```bash
# 给下一会话的开场白：
"接 P12，按 docs/HANDOFF_P12.md 推进"
```

会话开始后 AI 会：
1. 读 docs/HANDOFF_P12.md（本文件）
2. 读 memory/MEMORY.md
3. 询问：先跑 P11 构建验证 还是 直接进 P12 打磨

---

## 8. 没解决但要记住的小问题

- **caption 正则 bug**（P9 边界外）：`_FIG_CAPTION_RE` 要求 `Fig. N` 后跟 `.` 或 `:`，但实际 PDF 常见 `Fig. 1\nStudy Flowchart.`（数字后是换行）。已在 `feedback-figures-method-d.md` 记录，待将来单独 issue
- **start.bat 关窗 = 杀进程**：用户不能 minimize tray，关 cmd 窗口直接死。P12 可考虑 PowerShell 重写 + 托盘
- **/api/health 启动 30s 超时**：embedded Python 加载 anthropic SDK 在慢盘约 6s，余量充足；如果 ML 笔记本启动超时再调

---

## 9. 启动服务的命令（如果验证 P10 改动想看效果）

```powershell
# 后端
cd E:/projects/papercast-studio
D:/ana/envs/papercast-studio/python.exe -m papercast.server --port 8765 --log-level info

# 前端（另一个终端）
cd E:/projects/papercast-studio/webui
npm run dev
```

测试 paper：`448eb6cd01`（FPC-VLA），稳态停在 published。如果想从头看流水线，重跑 `scripts/p1_smoke.py`。
