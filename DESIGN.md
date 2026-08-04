---
name: AChat
description: Multi-agent collaboration workspace with IM-style chat experience
colors:
  primary: "oklch(0.588 0.166 257)"
  primary-dark: "oklch(0.646 0.157 257)"
  background: "oklch(0.985 0.002 280)"
  background-dark: "oklch(0.13 0.003 280)"
  foreground: "oklch(0.20 0.004 280)"
  card: "oklch(1 0 0)"
  card-dark: "oklch(0.17 0.003 280)"
  secondary: "oklch(0.961 0.003 280)"
  muted-foreground: "oklch(0.52 0.004 280)"
  accent: "oklch(0.94 0.003 280)"
  destructive: "oklch(0.52 0.16 25)"
  success: "oklch(0.55 0.10 150)"
  warning: "oklch(0.70 0.11 70)"
  border: "oklch(0.55 0.004 280 / 0.10)"
typography:
  body:
    fontFamily: "Manrope, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  heading:
    fontFamily: "Manrope, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.4
  label:
    fontFamily: "Manrope, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    letterSpacing: "normal"
  mono:
    fontFamily: "Geist Mono, ui-monospace, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
rounded:
  sm: "calc(0.375rem * 0.6)"
  md: "calc(0.375rem * 0.8)"
  lg: "0.375rem"
  xl: "calc(0.375rem * 1.4)"
  "2xl": "calc(0.375rem * 1.8)"
  "3xl": "calc(0.375rem * 2.2)"
  "4xl": "calc(0.375rem * 2.6)"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "oklch(1 0 0)"
    rounded: "{rounded.lg}"
    padding: "0.625rem"
    height: "2rem"
  button-primary-hover:
    backgroundColor: "oklch(0.588 0.166 257 / 0.8)"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.lg}"
    height: "2rem"
  button-destructive:
    backgroundColor: "oklch(0.52 0.16 25 / 0.1)"
    textColor: "{colors.destructive}"
    rounded: "{rounded.lg}"
    height: "2rem"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.xl}"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.foreground}"
    rounded: "{rounded.lg}"
    height: "2rem"
  badge:
    backgroundColor: "{colors.primary}"
    textColor: "oklch(1 0 0)"
    rounded: "{rounded.4xl}"
    height: "1.25rem"
---

# Design System: AChat

## Overview

**Creative North Star: "The Clarity Lab"**

AChat 的视觉系统是一台精密仪器：微冷中性灰作底，Apple System Blue 作信号，一切信息可扫描、可编排。它不追求情绪化的视觉冲击，而追求操作场景中的冷静可信——像 Apple 系统设置一样，你永远知道该看哪里、该点哪里。

系统的密度偏紧凑：按钮高度 2rem（32px），输入框同高，消息气泡内边距克制。这不是内容消费型产品，而是工作台——每个像素的留白都在为「同时管理 3+ Agent 对话」让路。动效存在但不抢戏：workspace 氛围动画周期 32–90 秒（几乎不可感知），DAG 调度特效只在任务运行时出现。所有动画遵守 `prefers-reduced-motion: reduce`。

暗色模式不是亮色的附属品——它有自己的色彩校准：背景 `oklch(0.13 0.003 280)`（#1C1C1E，不是纯黑），主色提亮到 `oklch(0.646 0.157 257)`（#0A84FF），阴影更重（0.2–0.3 vs 0.03–0.05），inset highlight 更弱（0.06 vs 0.6）。两套主题各自完整。

**Key Characteristics:**
- 微冷中性灰色温（hue ~280），不暖不冷
- 主色 System Blue 出现在 ≤10% 的屏幕面积，稀少即是分量
- 默认扁平，深度只来自状态（hover、elevation、focus）
- 紧凑密度：h-8 按钮与输入框，gap-1.5，px-2.5
- 动效分两层：交互层（150ms 过渡）+ 氛围层（32–90s 慢呼吸）
- 明暗双主题各自独立校准，非简单反转

## Colors

色板是一套微冷中性灰底 + Apple System Blue 主色 + 三个降饱和功能色的系统。所有颜色使用 OKLCH 定义，色温统一在 hue ~280（中性灰系）和 hue ~257（蓝色系）。

### Primary
- **System Blue** (oklch(0.588 0.166 257) ≈ #007AFF): 系统级信任色。出现在 CTA 按钮、链接、focus ring、active 状态指示。暗色模式提亮至 oklch(0.646 0.157 257)（#0A84FF）以补偿暗底对比度。不铺满——稀少即是分量。

### Neutral
- **Cool Paper** (oklch(0.985 0.002 280) ≈ #FBFBFD): 亮色模式背景。微冷白，不纯白，与纯白卡片做色阶分层。
- **Pure White Card** (oklch(1 0 0) = #FFFFFF): 卡片背景，与 Cool Paper 背景做 tonal layering。
- **Graphite** (oklch(0.20 0.004 280) ≈ #1D1D1F): 主文本色。近黑但微冷。
- **Fog Gray** (oklch(0.961 0.003 280) ≈ #F5F5F7): secondary / muted 背景。用于 hover、subtle 容器。
- **Slate Mist** (oklch(0.52 0.004 280) ≈ #86868B): muted-foreground。用于次要文本、placeholder、描述文字。
- **Accent Gray** (oklch(0.94 0.003 280) ≈ #EDEDEE): accent 背景，比 secondary 更浅。
- **Hairline** (oklch(0.55 0.004 280 / 0.10)): 边框线，10% 透明度的中性灰。极细极轻。

### Functional
- **Desaturated Red** (oklch(0.52 0.16 25)): destructive 色。降饱和处理，不刺眼。用于错误、删除、危险操作。
- **Desaturated Green** (oklch(0.55 0.10 150)): success 色。用于完成状态、成功提示。
- **Desaturated Orange** (oklch(0.70 0.11 70)): warning 色。用于等待、进行中、审批待办。

### Named Rules
**The One Voice Rule.** 主色 System Blue 出现在不超过 10% 的屏幕面积。它的稀少即是分量——CTA、链接、focus ring、active 指示，仅此而已。大面积使用蓝色等于自毁层级。

**The Cool Neutral Rule.** 所有中性灰色调的 hue 锁定在 ~280（微冷方向）。不偏暖不偏冷。暖灰或纯灰都是对色温一致性的破坏。

## Typography

**Body Font:** Manrope (system-ui, sans-serif fallback)
**Heading Font:** Manrope (同 body，通过 fontWeight 区分)
**Mono Font:** Geist Mono (ui-monospace, monospace fallback)

**Character:** Manrope 是一支几何无衬线，字宽适中、x-height 较高、开口圆润。它不追求个性表达，而追求在小尺寸（14px）下的可读性——符合工作台场景。Geist Mono 与 Manrope 的 x-height 匹配良好，用于代码块和工具输出。

### Hierarchy
- **Heading** (500, 1rem / 16px, 1.4): 卡片标题、对话框标题。通过 font-weight 500 而非更大字号建立层级。
- **Body** (400, 0.875rem / 14px, 1.5): 默认正文字号。消息文本、描述、表单内容。最大行宽不强制限制——这是工作台不是阅读 app。
- **Label** (500, 0.75rem / 12px, normal): badge、tag、元数据。不 uppercase——中文不需要。
- **Mono** (400, 0.875rem / 14px, 1.5): 代码块、bash 输出、JSON。与 body 同字号，仅字体不同。

### Named Rules
**The Single Family Rule.** 全系统只使用 Manrope（sans）和 Geist Mono（mono）两支字体。不引入 display 字体——14px 工作台 UI 不需要展示型字体。标题层级通过 weight 500 建立，不通过字体切换。

## Layout

布局是全视口工作台：`h-dvh overflow-hidden` 锁定视口高度，内部用 flex 分割。主布局是三栏：左侧 Sidebar（导航 + 会话列表）+ 中间主面板（聊天 / Agent / Artifact / 知识库等模式切换）+ 右侧叠加面板（文件浏览器、产物预览、任务详情等 overlay 弹出）。

间距使用 Tailwind 默认刻度，无自定义 spacing token。常见值：gap-1.5（6px）、px-2.5（10px）、p-4（16px）、gap-4（16px）。密度偏紧凑——这是操作场景，不是内容消费。

移动端：Sidebar 通过 `mobileSidebarOpen` 状态控制滑入滑出。viewport 使用 `interactiveWidget: 'resizes-content'` 让键盘弹起时收缩内容区而非覆盖。

Workspace 氛围背景使用极慢的 aurora/mesh 动画（32–90s 周期），目的是「让背景有生命力而非吸引注意力」。

## Elevation & Depth

**The Flat-By-Default Rule.** 默认扁平。深度只来自状态：hover、elevation、focus。

系统使用极轻阴影 + tonal layering 的混合策略。阴影透明度极低（亮色 0.03–0.05，暗色 0.2–0.3），远低于常规设计系统的 0.1–0.2。真正的深度感来自色阶分层：背景（#FBFBFD）→ 卡片（#FFFFFF）→ 次级容器（#F5F5F7），通过明度差异而非阴影建立层次。

### Shadow Vocabulary
- **Subtle Lift** (`box-shadow: 0 1px 3px 0 oklch(0 0 0 / 0.03), 0 1px 2px -1px oklch(0 0 0 / 0.03)`): 卡片、对话框的默认阴影。亮色模式下几乎不可见——存在但不被感知。
- **Raised** (`box-shadow: 0 4px 14px -2px oklch(0 0 0 / 0.05), 0 2px 6px -2px oklch(0 0 0 / 0.03)`): hover 或 elevated 状态。略重但仍克制。
- **Inset Highlight** (`inset 0 1px 0 0 oklch(1 0 0 / 0.6)`): 顶部内嵌高光。模拟光线从上方照射的微弱反射，亮色模式 0.6 透明度，暗色模式降至 0.06。卡片和对话框使用 `shadow-md + inset-hi` 组合。
- **Ambient Glow** (message-glow-pulse / dag-node-glow): 非阴影的状态指示。使用主色或功能色的 box-shadow 呼吸效果，指示「这个元素刚被关注」或「这个任务正在运行」。

## Shapes

圆角系统以 `--radius: 0.375rem`（6px）为基准，通过乘数生成 7 级递进。实际使用集中在三个档位：`rounded-lg`（6px，按钮/输入框）、`rounded-xl`（~8.4px，卡片/对话框）、`rounded-4xl`（~15.6px，badge 胶囊形）。

边框统一使用 1px Hairline（10% 透明度中性灰），不使用 2px 或更粗的边框。唯一例外是 focus ring：3px ring + border 同色，确保焦点状态清晰可见。

### Named Rules
**The Three-Tier Radius Rule.** 圆角只有三档：lg（6px，交互元素）、xl（8.4px，容器元素）、4xl（15.6px，胶囊元素）。中间档位（md, 2xl, 3xl）只在特殊场景使用。不要在同一层级混用不同圆角——同层同角。

## Components

### Buttons
- **Shape:** rounded-lg (6px)，border-transparent，bg-clip-padding
- **Primary:** bg-primary text-primary-foreground，h-8 px-2.5 gap-1.5 text-sm font-medium。hover 时 primary/80 透明度。
- **Ghost:** transparent 背景，hover 时 bg-muted。用于工具栏、次要操作。
- **Destructive:** bg-destructive/10 text-destructive，hover 时 destructive/20。非纯红底——降饱和的处理。
- **Focus:** border-ring + ring-3 ring-ring/50。3px ring + 同色 border。
- **Active:** translate-y-px（下移 1px）。物理反馈。
- **Disabled:** pointer-events-none opacity-50。

### Badge
- **Shape:** rounded-4xl（~15.6px，完全圆角胶囊），h-5 px-2 text-xs font-medium
- **Primary:** bg-primary text-primary-foreground
- **Secondary:** bg-secondary text-secondary-foreground
- **Destructive:** bg-destructive/10 text-destructive
- **Ghost:** hover:bg-muted，无默认背景

### Cards / Containers
- **Corner:** rounded-xl（~8.4px）
- **Background:** bg-card（亮色纯白，暗色 #2C2C2E）
- **Shadow:** shadow-md + inset-hi 组合（极轻阴影 + 顶部高光）
- **Border:** 无默认边框（通过色阶与背景分层）
- **Internal Padding:** p-4（16px），small 变体 p-3（12px）
- **Footer:** border-t bg-muted/50，与主体有色阶区分

### Inputs / Fields
- **Shape:** rounded-lg (6px)，1px border-input
- **Background:** transparent（暗色模式 bg-input/30）
- **Height:** h-8（32px），与按钮同高
- **Focus:** border-ring + ring-3 ring-ring/50
- **Error:** border-destructive + ring-destructive/20
- **Disabled:** bg-input/50 opacity-50

### Dialog
- **Shape:** rounded-xl（~8.4px）
- **Background:** bg-popover（亮色纯白，暗色 #2C2C2E）
- **Shadow:** shadow-md + inset-hi
- **Overlay:** bg-black/10 + backdrop-blur-xs
- **Animation:** fade-in + zoom-in-95（入场），fade-out + zoom-out-95（出场）
- **Close Button:** ghost variant，absolute top-2 right-2，icon-sm size

### Navigation (Sidebar)
- **Background:** bg-sidebar（亮色 #F5F5F7 附近，暗色 #1C1C1E 附近）
- **Active Item:** bg-sidebar-accent
- **Border:** sidebar-border（6% 透明度，比主 border 更轻）
- **Width:** 固定宽度，移动端滑入
- **Mode Switch:** 6 种 sidebar 模式（chat/agents/artifacts/cognition/extensions/resources），切换不动画

### Guide Floating Panel (Signature Component)
全局悬浮助手面板，双活跃会话模型的工作半边。支持拖拽、缩放、收起/展开、`Ctrl/Cmd+G` 快捷键唤起。位置和尺寸存 localStorage。移动端全屏覆盖。精简 MessageList 只渲染 text / tool_use / ask_user 三种 part——比主聊天面板更克制。

## Do's and Don'ts

### Do:
- **Do** 在亮色模式保持阴影透明度 0.03–0.05——这个系统的阴影是「存在但不被感知」的。
- **Do** 使用 tonal layering（色阶分层）建立深度，而非加重阴影。
- **Do** 在暗色模式提亮主色（#0A84FF vs #007AFF）和加重阴影（0.2–0.3 vs 0.03–0.05）。
- **Do** 为所有动画提供 `prefers-reduced-motion: reduce` 降级。
- **Do** 保持按钮和输入框同高（h-8 / 32px）——视觉节奏的基石。
- **Do** 使用 OKLCH 色彩空间定义颜色——保持色温和亮度的一致性。

### Don't:
- **Don't** 在大面积区域使用 System Blue——它的稀少即是分量（≤10% 屏幕面积）。
- **Don't** 使用暖灰或纯灰——所有中性灰的 hue 锁定在 ~280（微冷方向）。
- **Don't** 引入第三支字体——Manrope + Geist Mono 已覆盖全部场景。
- **Don't** 在工作台主场景使用快速/大面积动画——氛围动画周期 32–90s，交互动画 ≤600ms。
- **Don't** 使用 2px 或更粗的边框——1px Hairline 是这个系统的全部边框语言。
- **Don't** 在暗色模式使用纯黑背景——#1C1C1E 的微亮是刻意校准的。
- **Don't** 为标题引入 display 字体或更大字号——通过 font-weight 500 建立层级，不通过字号跳跃。
