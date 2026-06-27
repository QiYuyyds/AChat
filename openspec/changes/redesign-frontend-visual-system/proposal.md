## Why

当前桌面端视觉是「标准 shadcn + 字节蓝 + Geist」的默认组合：中性色全部为无色相纯灰（oklch chroma 0），背景是刺眼纯白，表面完全扁平、靠 hairline border 分隔，状态色散落在 7 个组件里以 25 处硬编码形式存在。结果是「干净但毫无识别度」——与市面上绝大多数 shadcn 仪表盘无法区分，也和多 Agent 协作工具应有的「工程、冷峻、有结构骨气」气质不匹配。

本变更新建一套名为「冷锋 / Cool-Edge」的视觉语言：把无色相纯灰换成极低 chroma 的冷调灰白（铝金属质感）、主色微调到同 hue 族的电光靛、圆角从 10px 收到 6px、面板分隔从 hairline 改为背景层次与内凹高光、active 态从色块填充改为左色条锚定、状态色归一到 `--success`/`--warning` 语义 token。目标是在不增加装饰、保持简约的前提下，让整体气质从「通用模板」变为「有识别度的工程仪表盘」。

## What Changes

- **新增设计 token 契约**：在 `globals.css` 引入冷调中性色（hue 250°、chroma 0.005–0.015）、电光靛主色（oklch 0.56 0.21 265）、`--success`/`--warning` 语义色、`--shadow-sm`/`--shadow-md`/`--inset-hi` 阴影 token，圆角基准从 0.625rem 收到 0.375rem。
- **基元层去描边走层次**：`ui/card` 从 `ring-1` 描边改为无描边 + `--shadow-md`；`ui/dialog` 同步；`ui/button` 圆角随 token 收紧，不增加新 variant。
- **业务组件硬编码色收敛**：将 7 个组件共 25 处 `bg-red-600`/`bg-green-50`/`bg-amber-50`/`text-[#3370FF]` 等硬编码全部替换为 `--destructive`/`--success`/`--warning`/`--primary` 语义 token 引用。
- **结构性视觉表达强化**：`message-item` 气泡去掉 border、改用背景层次 + 内凹高光，用户消息用 2px 主色左色条代替淡蓝底，meta 行改 mono 字体；`sidebar` active 态从灰底填充改为 2px 左色条 + 圆点锚定，tab 从蓝填充改为底部 2px 蓝线；`chat-panel` tab bar mono 化。
- **BREAKING（视觉语义，非 API）**：深浅色主题切换后整体观感显著变化，但不改变任何功能、数据、路由或交互逻辑。

## Capabilities

### New Capabilities
- `visual-system`: 桌面端设计 token 契约与视觉表达规则——定义色板（冷调中性色、电光靛主色、success/warning 语义色）、圆角刻度、阴影/内凹高光 token、组件去描边与层次规则、状态色归一约束、active 态色条锚定规范，作为所有 shadcn 基元与业务组件的单一视觉信息源。

### Modified Capabilities
<!-- 无。现有 frontend capability 的 requirement 全部是行为/架构契约（SSE 消费、状态管理、预览面板边界），不涉及视觉 token；本变更不改动任何现有 requirement 的行为语义。 -->

## Impact

- **代码**：`src/app/globals.css`（token 重写）、`src/components/ui/{card,dialog,button}.tsx`（基元微调）、`src/components/{message-item,sidebar,chat-panel,artifact-library,skill-library,document-detail,create-agent-dialog,new-conversation-dialog,upload-document-dialog}.tsx`（硬编码收敛与结构性表达）。
- **依赖**：无新增依赖。继续使用 Tailwind v4、shadcn、next-themes、Geist 字体；仅扩展 `@theme inline` 与 `:root`/`.dark` 变量。
- **移动端**：`apps/mobile/` 不受影响——移动端沿用独立 iOS 风格 token（`#f2f2f7` + glass blur），本变更显式不覆盖移动端，保持两端自洽。
- **主题**：next-themes 的 class 切换机制不变，仍为 system 默认 + 手动 toggle；仅 `.dark` 变量值更新。
