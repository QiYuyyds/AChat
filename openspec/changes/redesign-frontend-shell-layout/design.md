## Context

桌面端 shell 当前是 `page.tsx` 里 4 个 panel 平铺的单层 flex 行：

```
Sidebar(288) | ChatPanel(flex-1) | FileExplorerPanel(按需) | ArtifactPreviewPanel(按需)
```

`sidebar.tsx` 内部又分两段：顶部 6 个垂直 TabButton（mode 切换）+ 下方按 mode 分发的内容（会话列表 / 产物库 / Agent 库 / 分析 / 知识库 / 技能）。`fileExplorerOpen`、`previewArtifactId` 等开关已存在于 store，目前驱动「是否平铺出第三/四栏」。

目标调性：Linear/Vercel —— 单一焦点、细边框撑层次、彩色克制。

约束（用户明确）：
- **右上角 header 不动**：`chat-panel.tsx` 顶部那排（文件树/产物/文件库/大纲/加 Agent/用量/连接）外观保持原样。
- 不新增依赖、不改数据/SSE/路由契约、不影响移动端。

## Goals / Non-Goals

**Goals**
- 左栏一拆为二：导航（图标轨）与上下文（会话列表）物理分离。
- 副面板（文件树 / 产物预览）从平铺改为右侧滑入覆盖，主区恢复单一焦点。
- 彩色用法收敛到 CTA/选中态；边框对比与间距节奏微调。

**Non-Goals**
- 不重做右上角 header（外观、图标集合、分组都不动）。
- 不改 token 值（`--border` 一个值除外）—— 配色调色板归 `redesign-frontend-visual-system` 管。
- 不动移动端抽屉式 sidebar。
- 不改任何 store action 语义、SSE 事件、API 路由。

## Decision 1：左栏拆成「图标轨 + 会话列表」两栏

```
┌────┬─────────────┐
│ 56 │   240       │
│ 💬 │ AChat    │ ← 标题块整体搬到列表栏顶，内容不改，仅所在栏变窄
│ ▣  │ 多Agent协作 │
│ 🤖 ├─────────────┤
│ 📊 │ 搜索        │
│ 📖 │ 会话列表    │
│ ✨ │ ...         │
│ ── │             │
│ ⚙🌗│             │
└────┴─────────────┘
```

- 图标轨：6 个 mode 各一个图标按钮，active 用 2px 左色条 + 主色锚定（沿用 visual-system 的 active 表达），hover 出 tooltip（label）。⚙/🌗 沉到轨底。
- 第二栏宽度 240（原 288，因导航已外移）。
- collapsed 态简化：图标轨常驻，第二栏可整体收起（替代原 `collapsed` 双形态逻辑，减一套分支）。
- AChat 标题块（`h1` + 副标题）原样保留，只是 DOM 上落到第二栏顶部。

**为什么不做右上角 [视图▾]**：用户明确要求右上角不动；原探索方案里的 header 收纳（[视图▾]/[⋯]）撤销。

## Decision 2：副面板改右侧滑入覆盖

- `page.tsx` 不再把 `FileExplorerPanel`/`ArtifactPreviewPanel` 作为 flex 兄弟平铺，改为绝对定位的右侧 overlay（`fixed/absolute inset-y-0 right-0`），带半透明遮罩，点遮罩或 Esc 关闭。
- 进出场用 CSS transition（`translate-x`），复用 sidebar 移动端抽屉同款手法（`transition-[transform]`）。
- 触发开关不变：仍读 `fileExplorerOpen` / `previewArtifactId`；右上角 header 按钮的 onClick 不改（已是 setState）。**呈现层换皮，状态层不动。**
- 文件树与产物预览互斥关系沿用现状（已是互斥 toggle）。

**权衡**：overlay 覆盖会临时挡住主区。可接受 —— Linear/VSCode 的副面板也是覆盖/可关，单一焦点优先于「同时看见全部」。宽屏若想并排，留作后续增强（非本变更范围）。

## Decision 3：彩色用法收敛（不改调色板）

- primary（电光靛）只保留在：主 CTA（新建对话等）、当前选中态（active tab/会话）。未选中 tab、普通图标、pin/ring 等非关键处降级为 `muted-foreground`/中性。
- `--border` 略提对比（唯一一处 token 值调整），让细边框能独立撑层次，减少对阴影的依赖。
- 间距：会话列表项、tab、header 的 `py-1.5`/`py-2` 紧凑值放宽一档，给呼吸感。

## Risks

- **R1 overlay 与现有 z-index / popover 冲突**：选择弹层、tooltip 需确认层级。缓解：overlay 用与移动端抽屉一致的 z 段（z-30 遮罩 / z-40 面板），逐个回归。
- **R2 collapsed 逻辑重构回归**：原 sidebar 的 `collapsed` 双形态分支多，拆栏后要重写。缓解：图标轨常驻使 collapsed 只剩「第二栏开/合」一个维度，复杂度反而下降。
- **R3 与 visual-system 改动叠加**：两份 change 都碰 `sidebar.tsx`。缓解：本变更只在该分支上叠加，token 值不重定义，仅调 `--border` 与彩色引用位置。

## Migration

纯前端呈现层重构，无数据迁移。逐组件改 + `pnpm typecheck`/`pnpm lint` 把关；overlay 与图标轨交互手动回归（开/关/互斥/Esc/遮罩/移动端不受影响）。
