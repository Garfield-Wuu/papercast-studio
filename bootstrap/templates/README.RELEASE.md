# papercast-studio · 安装与使用说明

> 把 PDF 论文一键转成 ~8 分钟的实验室分享视频。本目录是 Windows 便携式发布包，解压即用。

---

## 1. 系统要求

- Windows 10 或更高版本（x64）
- 4GB 以上空闲磁盘空间（含 LibreOffice）
- 8GB 以上内存
- 稳定的网络（首次安装下载 LibreOffice ~250MB；运行流水线时调用 LLM / TTS 云服务）

---

## 2. 第一次启动

### 2.1 解压

把 `papercast-studio-x.x.x-win-x64.zip` 解压到任意目录。**不要放在 OneDrive / Dropbox 等同步目录**，路径里也避免空格和中文。推荐路径：

```
D:\papercast-studio\
```

### 2.2 装 LibreOffice（一次）

打开解压后的目录，**右键** `install.ps1` → **使用 PowerShell 运行**：

- 自动下载 LibreOffice portable（~250MB）到 `runtime\libreoffice\`
- 不会修改系统 PATH 或注册表
- 已经装过的用户跑这步会自动跳过

> ⚠️ 如果 PowerShell 提示「执行策略」错误，先打开 PowerShell（普通权限就行）粘贴：
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

### 2.3 填密钥

打开 `config\secrets.env`，按文件内的注释填好：

- `ANTHROPIC_API_KEY` 或类似的 LLM 厂商密钥
- `MINIMAX_API_KEY`（用于 TTS）

也可以先双击 start.bat 跑起来，到 WebUI 的「配置」页用密码框输入。

### 2.4 启动

双击 `start.bat`。

- 启动后端：监听 `http://127.0.0.1:8765`
- 自动打开 Edge 浏览器（App 模式）
- 没装 Edge 会自动用系统默认浏览器

第一次启动 5-10 秒；后端日志在 `start.bat` 的黑窗口里。

> 关闭那个黑窗口 = 关闭服务。

---

## 3. 目录结构

```
papercast-studio\
├── start.bat               ← 双击启动
├── install.ps1             ← 第一次跑（装 LibreOffice）
├── config\
│   ├── config.yaml         ← LLM / TTS / 视频参数（也可在 WebUI 改）
│   ├── secrets.env         ← API 密钥
│   └── voices.json         ← 音色收藏（自动创建）
├── inbox\                  ← 拖 PDF 进来
├── archive\                ← 已注册的论文原文备份
├── work\                   ← 流水线中间产物（每个论文一个子目录）
├── review\                 ← 待审阅的演示稿
├── output\                 ← 最终视频
└── logs\                   ← 运行日志 + 数据库
```

---

## 4. 完整流程

1. **双击** `start.bat`，浏览器自动打开
2. 进入「**工作区**」，把 PDF **拖进上传区**
3. 弹窗里填**汇报日期 / 汇报人 / 专业** → 点「启动流水线」
4. 流水线自动跑：解析 → 切图 → LLM 精读 → 生成 PPT 与讲稿
5. 进入「**待审阅**」页 → 点对应论文 → 5 Tab 审阅面板（切图 / PPT 讲稿 / 事实卡）
   - 不勾选 = 通过该项；勾选并写反馈 → 局部重生
   - 全部 OK 后点右上「**全部通过 →**」
6. 进入 TTS 与视频合成（5-15 分钟，看论文长度）
7. **完成** → 「**文件管理**」页找最终 mp4 下载

---

## 5. 常见问题

### Q1：start.bat 黑窗口闪退

打开 PowerShell，进入解压目录后手动运行：

```powershell
.\start.bat
```

错误信息会停在窗口里不消失。

### Q2：LLM 调用失败

去「**配置**」页：
- 选 Provider（Anthropic / OpenAI / DeepSeek / Qwen / …）
- 填 API Key
- 点「**测试连通性**」按钮

### Q3：「找不到 LibreOffice」错误

跑一次 `install.ps1`。如果还不行，手动检查 `runtime\libreoffice\program\soffice.exe` 是否存在。

### Q4：视频出来但音频是默认 voice

去「**语音管理**」页：
- 系统音色右边点 ⭐ 加进收藏（或者用克隆向导上传自己的样本）
- 回到「**配置**」页，**音色**下拉里选刚收藏的项 → 保存

### Q5：怎么升级到新版本

下新版 zip 解压到**新目录**，把老目录的 `config\` `inbox\` `archive\` `work\` `review\` `output\` `logs\` 整个拷过去（保留任务历史）。

---

## 6. 卸载

- 关闭 start.bat 的黑窗口
- 直接删除整个解压目录
- 不留任何注册表 / 环境变量痕迹

---

## 7. 反馈

GitHub: https://github.com/Garfield-Wuu/papercast-studio
