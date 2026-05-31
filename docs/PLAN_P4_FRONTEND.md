# P4 — 前端骨架开发计划

> 在 P2 后端之上建一个能跑的 React + Vite 前端，验证 12 阶段进度可视化、WS 事件流、papers/artifacts 接口集成。**不**做审阅 Tab（留给 P5）；**不**做完整文件管理（留给 P6）。目标是「上传 PDF → 看到流水线动起来 → 跑到 awaiting_review 停下」这一段可视化跑通。

---

## 决定（一些必要的小选型）

1. **构建器**：Vite 5 + React 18 + TypeScript 5。这是 ui-ux-pro-max 推荐的默认；和 shadcn/ui 兼容最好。
2. **路由**：`react-router-dom` v7。三页：列表 / 详情 / 设置。后续 P6 加文件管理时再加。
3. **样式 / 组件**：Tailwind CSS + shadcn/ui（按需复制组件，不当 npm 依赖装）。
4. **数据层**：TanStack Query v5（papers / artifacts / config 列表 + 缓存 + 刷新）。
5. **状态**：纯本地组件 state；少量跨页只读偏好用 zustand 单 store（可选，不强制）。
6. **WebSocket**：原生 `WebSocket` + 一个 `usePaperEvents` hook 自动订阅 / 重连。
7. **类型**：openapi-typescript 直接从 `/openapi.json` 生成 `webui/src/lib/api.gen.ts`，前后端类型对齐。
8. **包管理器**：pnpm。
9. **路径别名**：`@/` 指 `webui/src`。
10. **不引入**：MSW（手工 stub 后端就够；后端本来就跑得动）、CRA、Storybook（P8 再说）、SSR（无意义）。

---

## 视觉系统（按 ui-ux-pro-max 检索结果落实）

直接用 skill 反复确认得到的「Data-Dense + Drill-Down Analytics + Dark Mode 选项」组合。token 写到 `tokens.css` 里：

```css
/* webui/src/styles/tokens.css */
:root {
  /* 基础间距 — 8px 网格 */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px;
  --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --space-8: 48px; --space-10: 64px;

  /* 排版 — Inter (UI) + Source Han Sans CN (中文) + JetBrains Mono (代码/JSON) */
  --font-sans: "Inter", "Source Han Sans CN", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --text-xs: 12px;     /* 表格行内、辅助 */
  --text-sm: 14px;     /* 次级文字、按钮 */
  --text-base: 15px;   /* 默认正文 */
  --text-lg: 18px;     /* 卡片标题 */
  --text-xl: 22px;     /* 页面 H1 */
  --text-2xl: 28px;    /* 任务详情大标题 */

  /* 颜色 — 浅色面板（默认）；深色见 [data-theme="dark"] */
  --color-bg: oklch(99% 0.005 240);
  --color-surface: oklch(98% 0.005 240);
  --color-surface-2: oklch(95% 0.008 240);
  --color-border: oklch(88% 0.01 240);
  --color-text: oklch(20% 0.02 240);
  --color-text-muted: oklch(45% 0.015 240);
  --color-accent: oklch(58% 0.18 250);              /* progress blue */
  --color-accent-soft: oklch(92% 0.06 250);

  /* 状态色（WCAG AA 4.5:1） */
  --color-success: oklch(62% 0.16 145);
  --color-warning: oklch(72% 0.16 75);
  --color-danger:  oklch(58% 0.22 25);
  --color-pending: oklch(70% 0.005 240);             /* 灰色 */

  /* 容器 / 阴影 */
  --radius: 8px;
  --radius-lg: 12px;
  --shadow-sm: 0 1px 2px 0 oklch(20% 0.02 240 / 0.06);
  --shadow-md: 0 4px 12px 0 oklch(20% 0.02 240 / 0.08);
  --duration: 200ms;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
}

[data-theme="dark"] {
  --color-bg: oklch(15% 0.01 240);
  --color-surface: oklch(18% 0.012 240);
  --color-surface-2: oklch(22% 0.015 240);
  --color-border: oklch(28% 0.015 240);
  --color-text: oklch(95% 0.005 240);
  --color-text-muted: oklch(70% 0.01 240);
  --color-accent: oklch(70% 0.18 250);
  --color-accent-soft: oklch(35% 0.12 250);
  --color-success: oklch(70% 0.18 145);
  --color-warning: oklch(78% 0.18 75);
  --color-danger:  oklch(68% 0.22 25);
  --color-pending: oklch(45% 0.005 240);
}
```

主题切换默认跟系统 (`prefers-color-scheme`)，header 右上角一个 toggle。

排版方向 = **Data-Dense + Drill-Down**：
- 顶部 60px header（logo + 任务计数 + theme toggle）
- 主区 8px / 12px / 16px 三档间距交替（sm/base/relaxed）
- 进度条按 12 阶段平铺，状态用色而非 emoji；hover 显示阶段细节卡

---

## 目录结构

```
webui/
├── package.json                    # vite, react, ts, tailwind, shadcn 依赖
├── vite.config.ts                  # 端口 5173，proxy /api/* 和 /ws/* 到 :8765
├── tsconfig.json                   # @/ 路径别名
├── tailwind.config.ts              # 把 tokens.css 的变量映射到 tailwind 颜色
├── postcss.config.js
├── index.html
├── public/
└── src/
    ├── main.tsx                    # 入口：QueryClient + RouterProvider + ThemeProvider
    ├── App.tsx                     # 路由壳 + Header
    ├── lib/
    │   ├── api.ts                  # fetch wrapper + base URL，组装 /api/*
    │   ├── api.gen.ts              # openapi-typescript 生成（gitignored 也可以）
    │   ├── ws.ts                   # WebSocket 自动重连
    │   └── stage.ts                # 12 阶段元数据（label / icon / color）
    ├── hooks/
    │   ├── usePaperEvents.ts       # 订阅 /ws/papers/{pid}
    │   ├── usePapers.ts            # GET /api/papers + invalidate on event
    │   ├── usePaperDetail.ts       # GET /api/papers/{pid}
    │   └── useTheme.ts             # 持久 theme to localStorage
    ├── components/
    │   ├── ui/                     # shadcn 组件（button, card, input, table, dialog, ...）
    │   ├── layout/
    │   │   └── Header.tsx
    │   ├── pipeline/
    │   │   ├── PipelineProgress.tsx        # 12 阶段进度条主体
    │   │   ├── StageDot.tsx                # 单个阶段圆点 + tooltip
    │   │   └── EventLog.tsx                # WS 日志区（aria-live=polite）
    │   ├── papers/
    │   │   ├── PaperList.tsx               # 任务列表（table + 状态 chip）
    │   │   └── UploadDropzone.tsx          # 拖拽上传 PDF
    │   └── empty/EmptyState.tsx
    ├── pages/
    │   ├── PapersPage.tsx                  # / 路由 → 列表 + 上传区
    │   ├── PaperDetailPage.tsx             # /papers/:pid → 进度条 + 日志 + 操作按钮（start/stop/retry）
    │   └── SettingsPage.tsx                # /settings → 简化版 GET /api/config + secrets fingerprint
    └── styles/
        ├── tokens.css
        ├── typography.css
        └── global.css
```

---

## 后端协作

vite.config.ts 的 dev proxy：
```ts
server: {
  port: 5173,
  proxy: {
    "/api": "http://127.0.0.1:8765",
    "/ws":  { target: "ws://127.0.0.1:8765", ws: true },
  },
}
```

dev 时前端 5173 → 代理到后端 8765；P7 打包时前端 build 到 `papercast/server/static/` 由 FastAPI 同源 serve。本阶段不动后端，只在 dev 模式跑。

---

## P4 范围内要交付的功能

| 功能 | 接口 | 验收 |
|---|---|---|
| 顶部 header + theme toggle | — | 切换 light/dark 持久化 |
| 任务列表 | `GET /api/papers` | 表格显示 paper_id / filename / stage chip / ingested_at |
| 拖拽上传 PDF | `POST /api/papers` | 拖入 → 上传进度 → 列表自动刷新 |
| 删除任务 | `DELETE /api/papers/{pid}` | 行内菜单确认对话 |
| 任务详情 | `GET /api/papers/{pid}` | 12 阶段进度条 + 当前 stage 高亮 + history 时间轴 |
| 操作按钮 | `POST /start /stop /retry` | 按钮按 stage 启用/禁用 |
| WS 事件流 | `/ws/papers/{pid}` | 事件实时追加到日志区；阶段动画自动推进 |
| 设置页（只读） | `GET /api/config` | 显示当前 LLM provider / model / secrets fingerprint |
| 健康指示 | `GET /api/health` | header 上一个绿/黄点 |

**不做**（留 P5/P6）：
- 审阅 Tab（5 页 figures/reading/slides/script/facts）
- Monaco 编辑器
- regenerate / approve UI
- 文件树 / inbox/work/output 浏览
- 配置编辑 / 音色克隆

---

## 实施顺序

| 子步 | 内容 | 估时 |
|---|---|---|
| **P4.1** | Vite 工程脚手架 + Tailwind + shadcn init + tokens.css + 路径别名 + dev proxy | 30 min |
| **P4.2** | api.ts + ws.ts + 类型生成（openapi-typescript） + 基础 layout/header + theme | 45 min |
| **P4.3** | PapersPage：列表 + 拖拽上传 + 删除 + Health 指示器 | 1 h |
| **P4.4** | PaperDetailPage：12 阶段进度条 + WS 日志区 + 操作按钮 | 1.5 h |
| **P4.5** | SettingsPage（只读）+ EmptyState + 边界态打磨（无任务 / 离线 / 上传失败） | 30 min |
| **P4.6** | 跑通 e2e：dev 起前后端，拖一篇 PDF 进去看动画 + 文档 + commit | 30 min |

合计 ~4-5 小时（含 npm install 等待）。

---

## 风险

| 风险 | 缓解 |
|---|---|
| pnpm 离线下载慢 / 失败 | 用国内镜像（淘宝 npmmirror）；如装不上换 npm |
| openapi-typescript 跑不通 | 手工写一份精简 types.ts；后续 P5 再补 codegen |
| WebSocket 在 vite proxy 下不稳定 | proxy ws 标志 + 指定 origin；测试明确确认收到 events |
| shadcn/ui 组件污染样式 | 全部用 token CSS 变量；shadcn 组件配置 `cssVariables: true` |
| 浏览器兼容（OKLCH） | Chrome 111+ / Firefox 113+ / Safari 16.4+ 都支持；课题组都用现代浏览器 |
| 大文件上传体验 | 先做基础 multipart；进度条 P5 再补 |

---

## 文档更新

P4 完成后：
- 更新 `docs/PLAN_WEBUI.md` 把 P4 标 ✅
- 新建 `docs/FRONTEND.md` — 前端工程指南（dev/build/路由/数据层/视觉 token）
- 更新 `README.md` 加 webui 启动章节
- 沉淀 `feedback-frontend-architecture.md` memory

---

## 立即可启动

我现在按 P4.1 起步。中途如果代理还没来不影响前端开发；本地装 pnpm 包不需要代理（npmmirror 有镜像）。前端写完一起 push。
