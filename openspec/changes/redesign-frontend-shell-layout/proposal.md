## Why

`redesign-frontend-visual-system` 已经把 token / 配色迁到 oklch「冷锋」语言，但**桌面端骨架结构没动**：左栏一根 288px 柱子同时干两件事（顶部 6 个垂直 tab 做全局导航 + 下方做会话列表），主区右侧的文件树与产物预览是**常驻平铺第三/四栏**（`page.tsx` 是 `Sidebar | Chat | File | Artifact` 四栏 flex）。

结果是「干净但没层次」：导航和上下文挤在同一根柱子里密度过高；四栏平铺时主聊天区被两侧挤压、失去单一焦点；彩色（电光靛 primary）散落在未选中 tab、ring、pin 等非关键处，画面发「吵」。这些是结构问题，不是 token 问题，上一份 change 解决不了。

本变更按 Linear/Vercel 调性重做桌面端骨架：左栏一拆为二（窄图标轨 + 会话列表）、副面板改为右侧滑入覆盖以还主区单一焦点、把彩色用法收敛到 CTA/选中态。**显式不动右上角 header**（视图切换/状态/加 Agent/用量/连接那一排，含其纯图标外观），只改它触发的副面板呈现方式。

## What Changes

- **左栏一拆为二**：现有 `sidebar.tsx` 顶部 6 个垂直 tab（对话/产物库/Agents/分析/知识库/技能）抽成独立的 56px **图标轨**（active 指示条 + hover tooltip，⚙设置/🌗主题沉到轨底）；会话列表（含 AgentHub 标题块、搜索、列表、归档区）独占第二栏 240px。导航与上下文不再共用一根柱子。
- **副面板改滑入覆盖**：`page.tsx` 的 `FileExplorerPanel` / `ArtifactPreviewPanel` 从常驻平铺改为**从右侧滑入的 overlay**（覆盖在主区之上，带遮罩/可点外部关闭），不再挤压主聊天区。触发按钮仍是右上角 header 现有的那几个图标，**外观不变，仅行为从「展开平铺栏」改为「滑入覆盖」**。
- **彩色用法收敛**：把散落在非关键处的 `text-primary`/`ring-primary`/`bg-warning/10` 等彩色降级为中性灰，电光靛只保留在主 CTA 与当前选中态；`--border` 略提对比以靠细边框（而非阴影）撑 figure-ground；列表项/段落间距从 `py-1.5`/`py-2` 紧凑值放宽，给出呼吸节奏。
- **显式不变（约束）**：右上角 header 的内容、图标集合与视觉外观保持原样；不新增依赖；不改任何 store/SSE/路由/数据契约；`apps/mobile/` 不受影响。

## Capabilities

### Modified Capabilities
- `frontend`: 新增桌面端 shell 布局结构契约（三区：图标轨导航 / 会话列表 / 工作区主区）与副面板「右侧滑入覆盖」呈现规则。现有「Artifact preview SHALL be separate from chat rendering」requirement 的语义不变（预览仍在独立面板、仍由 artifact_ref 触发），仅呈现形态由平铺改为 overlay。
- `visual-system`: 新增「彩色用量收敛」与「边框对比/间距节奏」表达规则，约束 primary 仅用于 CTA/选中态。

## Impact

- **代码**：`src/app/page.tsx`（四栏 flex → 三区 + overlay 容器）、`src/components/sidebar.tsx`（拆出图标轨 + 会话列表两部分）、`src/components/file-explorer-panel.tsx` 与 `src/components/artifact-preview-panel.tsx`（改 overlay 呈现 + 进出场动画）、相关 store 字段（`fileExplorerOpen`/`previewArtifactId` 语义不变，仅消费方式从「占位栏」改为「overlay 开关」）。彩色收敛涉及 `sidebar.tsx` 等组件 className 调整。
- **右上角 header**：`chat-panel.tsx` header 那排按钮**不改外观**，仅其 onClick 行为指向 overlay 开关（若已是 store 开关则无需改）。
- **依赖**：无新增。继续 Tailwind v4 / shadcn / next-themes；overlay 动画用 CSS transition + 现有 `cn`。
- **移动端**：不受影响。移动端 sidebar 已是抽屉式（`max-md:fixed` translate），本变更只重排桌面端（`md:` 以上）结构。
- **与 `redesign-frontend-visual-system` 的关系**：正交。那份定 token 值，这份定结构与用法；本变更不修改任何 token 定义，只调整 `--border` 一个值与彩色引用位置。
