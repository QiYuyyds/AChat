## Context

桌面端前端基于 Next.js 16 + Tailwind v4 + shadcn/ui + next-themes，所有设计 token 集中在 `src/app/globals.css` 的 `@theme inline` 与 `:root`/`.dark` 块。当前 token 体系是 shadcn 默认值加字节蓝主色：中性色全部 `oklch(* 0 0)`（无色相纯灰）、`--radius: 0.625rem`、card 与 background 同值（`oklch(1 0 0)`）、无阴影 token、无 success/warning 语义色。shadcn 基元（`ui/card` 用 `ring-1 ring-foreground/10`、`ui/dialog` 同样）与约 25 处业务硬编码色（`bg-red-600`/`bg-green-50`/`bg-amber-50`/`text-[#3370FF]`）共同构成当前视觉。移动端 `apps/mobile/` 使用独立 iOS 风格 token（`#f2f2f7` + glass blur），与桌面不同源。

## Goals / Non-Goals

**Goals:**
- 让桌面端视觉从「通用 shadcn 模板」变为「有识别度的冷调工程仪表盘」，气质与多 Agent 协作工具匹配。
- 把视觉契约固化为一等公民 capability（`visual-system`），使未来改版有据可循、可测试。
- 消除 25 处硬编码状态色，统一到 `--destructive`/`--success`/`--warning`/`--primary` 语义 token。
- 保持简约：不增加装饰、不动功能/数据/路由/交互逻辑。

**Non-Goals:**
- 不改移动端（`apps/mobile/`）视觉，保持 iOS 风格自洽。
- 不引入新依赖、新字体、新图标库。
- 不改变 next-themes 的 class 切换机制或深浅色数量。
- 不重构组件结构或布局（4 栏 IDE 布局不变）。
- 不改代码块语法高亮主题（github theme 暂留，后续单独评估）。

## Decisions

### 决策 1：中性色采用极低 chroma 冷调（hue 250°，chroma 0.005–0.015）而非纯灰或暖灰
**Rationale**：当前中性色 `chroma = 0` 在 LCD 上偏暖偏脏，是「廉价感」主因。给中性色加 0.008–0.015 的极低 chroma、hue 拉到 250°（蓝灰方向），视觉上从「白纸」变「铝板」，成本几乎为零（仅改变量值）。冷调与「工程、冷峻」气质一致。
**备选**：① 保持纯灰（无提升）；② 暖灰 hue 40°（偏文档气，与协作工具冲突）；③ 彩色背景（破坏简约）。

### 决策 2：主色微调到电光靛 `oklch(0.56 0.21 265)` 而非彻底换色或保持原值
**Rationale**：原值 `oklch(0.591 0.222 263.57)` 偏亮偏轻浮；调到 0.56 同 hue 族更深更冷，提升质感且保持品牌延续（仍属字节蓝家族，hex 接近）。彻底换 hue（如靛紫 280°）会破坏老用户的品牌认知。
**备选**：① 保持原值（无质感提升）；② 换靛紫 hue 280°（破坏品牌）；③ 降饱和雾蓝（识别度不足）。

### 决策 3：圆角基准 0.625rem → 0.375rem（10px → 6px），不更锐或更圆
**Rationale**：6px 在「工程感」与「不显紧张」之间平衡。控件随 token 自动收到 ~4px。更锐（2–4px）偏 brutalist 可能压抑；更圆（14px+）偏消费/文档气，与工具气质不符。
**备选**：① 4px（过硬）；② 8px（折中但提升有限）；③ 14px（过柔）。

### 决策 4：面板分隔从 hairline border 改为背景层次 + 1px 内凹高光
**Rationale**：hairline 在扁平表面上是唯一层次来源，去掉后需替代。`bg-card` 略亮于 `bg-background`（light 模式 card=`oklch(1 .005 250)` vs background=`oklch(.99 .008 250)`）+ `--inset-hi: inset 0 1px 0 0 oklch(1 0 0 / 0.6)` 制造「嵌入式面板」铝金属质感。`ui/card`/`ui/dialog` 从 `ring-1` 描边改为无描边 + `--shadow-md`。
**备选**：① 保留 hairline（无变化）；② glass backdrop-blur（4 栏密集布局下易糊成一团）；③ 纯阴影无边框（dark mode 下阴影不可见，需配合内凹高光）。

### 决策 5：active/hover 态从色块填充改为左色条锚定
**Rationale**：填充占视觉重量过大、列表项多时显重；2px 左色条 + 透明底识别度高且轻，已被 Linear 验证。`sidebar` 会话 active 态从 `bg-accent` 改为 `border-l-2 border-primary` + 头像旁小圆点；tab 从 `bg-primary` 填充改为底部 2px 蓝线 + 文字提色。
**备选**：① 保留填充（重）；② 纯文字提色无色条（识别度不足）；③ 右色条（与阅读方向相悖）。

### 决策 6：状态色归一到 `--success`/`--warning` 语义 token
**Rationale**：25 处 `bg-red-600`/`bg-green-50`/`bg-amber-50` 等硬编码不可维护、不可主题化。新增 `--success: oklch(0.60 0.16 145)`（薄荷绿，冷调）与 `--warning: oklch(0.72 0.15 75)`（琥珀，唯一暖点），danger 仍走现有 `--destructive`（火山红 hue 30）。归一后主题切换一致、可测试。
**备选**：① 保留硬编码（现状）；② 用 Tailwind 原生 `red-600` 等（不可随主题切换，与 token 体系割裂）。

### 决策 7：移动端显式隔离，不同步改版
**Rationale**：`apps/mobile/` 的 iOS token 体系（`#f2f2f7` 分组背景 + glass blur + iOS 系统色）已自洽且成熟，强行对齐桌面冷调会两边都不像。视觉是两端独立进化的维度，本变更显式不覆盖移动端。
**备选**：① 同步改（翻倍工作量且破坏 iOS 自洽）；② 移动端改用桌面 token（失去 iOS 原生感）。

## Risks / Trade-offs

- **[圆角收紧后组件整体变「硬」]** → 可在实施时将基准回调到 0.5rem（8px）折中；控件层级保持自动跟随。
- **[dark mode 下阴影不可见]** → `--shadow-md` 在 dark 模式用 `oklch(0 0 0 / 0.3–0.4)` 深度，配合 `--inset-hi` 内凹高光保证层次可读。
- **[mono 字体比重提高，中文无 mono 不受益]** → mono 仅用于拉丁文与数字 meta（时间、token 计数、tab label、status），中文正文/标题仍走 sans，避免混排违和。
- **[warning 与 destructive 区分度]** → warning 用 hue 75 琥珀、destructive 用 hue 30 火山红，色相差距足够；且 warning 默认用于「提示/本地工作目录」，destructive 用于「删除/撤回」，语义场景不重叠。
- **[去 hairline 后面板边界模糊]** → 背景层次差值（card vs background）需保持在 oklch 0.01–0.015 亮度差，低于此则层次消失。

## Migration Plan

四层递进，每层可独立验收与回滚：

1. **Token 重写**（`globals.css`）：替换 `:root`/`.dark` 色板、圆角、新增阴影/语义色 token。验收：深浅色切换无报错，吃 token 的 shadcn 基元自动联动。
2. **基元微调**（`ui/card`、`ui/dialog`、`ui/button`）：card/dialog 去 ring 改 shadow + inset-hi。验收：dialog/card 视觉层次正常。
3. **业务硬编码收敛**（7 组件 25 处）：替换为语义 token。验收：全局 grep 无残留硬编码状态色。
4. **结构性表达**（`message-item`、`sidebar`、`chat-panel`）：气泡去 border + 左色条 + mono meta；sidebar 色条锚定；tab mono 化。验收：整体气质成型。

回滚策略：每层均为独立提交，token 层可单独回滚至 shadcn 默认值而不影响后续层（后续层在 token 回滚后仅表现为「无 shadow/左色条但功能正常」）。

## Open Questions

- 主色亮度最终取 `0.56` 还是回调到 `0.58`（更接近原 #3370FF）？倾向 0.56，实施时可并排对比后定。
- 代码块（shiki github theme）是否同步冷调？本变更暂留，作为后续独立小变更评估，避免 scope 蔓延。
