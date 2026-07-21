# Design: Chat UI Refinement

## Context

当前 `MessageItem` 用一个外层 `div` 包裹所有 parts，给整条消息一个 `bg-card` 气泡。`PartList` 只对连续 `tool_use` 做聚类（`ToolCluster`），其余 part 类型平铺渲染。结果是：一条典型 Agent 回复可能包含 thinking + 多个 tool_use + 多段 text 交替，每个 part 都是独立卡片块，视觉权重相同，长对话中用户难以快速定位文字结论。

字体方面，`layout.tsx` 加载 `Geist`（latin only），中文字符回退到系统字体，且 Geist 的几何冷感和暖灰米主题不搭。元信息行（name + time + token）用 `font-mono`（Geist Mono），让界面偏代码编辑器风格。

## Goals / Non-Goals

**Goals:**
- 长对话中用户能一眼扫到 Agent 的文字结论，工具调用过程不抢视觉焦点
- 字体从 Geist 换为 Manrope，与暖灰米主题气质一致
- 气泡内部有足够的呼吸空间（padding 增大）
- 保留暖灰米主题色板不变，只微调对比度

**Non-Goals:**
- 不改后端、DB schema、StreamEvent 协议
- 不改 message parts 数据结构（`MessagePart` 类型不变）
- 不改暖灰米主题的色板定义
- 不改 ChatPanel header 的布局（header 拥挤问题本次不处理）
- 不改 MessageInput 输入框样式

## Decisions

### D1: PartList 双段聚类（过程段 / 结论段）

**决策**：PartList 遍历 parts 时，将连续的「过程类型 part」归为一个 `ProcessSegment`，「结论类型 part」各自独立。

```
part 类型分类：
  过程型: thinking, tool_use, file_write_preview
  结论型: text, artifact_ref, deploy_status, execution_plan, deploy_candidates,
          image_attachment, file_attachment
```

聚类算法：线性遍历 parts，维护当前过程段缓冲区。遇到过程型 part 追加到缓冲区；遇到结论型 part 先 flush 缓冲区为一个 ProcessSegment，再渲染结论 part。末尾再 flush 一次。

**为什么不用更复杂的嵌套**：过程段和结论段的交替顺序天然保留在原位，不打乱叙事结构。用户看到的是「过程→结论→过程→结论」的节奏，而不是「所有过程打包→所有结论打包」。

**替代方案考虑**：把所有过程段汇总到消息顶部一个折叠块——否决，因为会丢失「先做了什么再说结论」的时间叙事。

### D2: 过程段折叠行为

**决策**：`ProcessSegment` 的展开/折叠状态由 `message.status` 驱动：

```
message.status === 'streaming'  →  过程段展开（无框小字）
message.status !== 'streaming'  →  过程段折叠为一行摘要
```

折叠摘要格式：`▸ [thinking 耗时] · N 个工具 · [总耗时]`

- 包含 thinking 时：`▸ 思考 3.2s · 2 个工具 · 5.3s`
- 不含 thinking 时：`▸ 2 个工具 · 2.1s`
- 只有 thinking 时：`▸ 已深度思考 3.2s`

用户点击折叠摘要可手动展开查看详情。展开后内部渲染保持无框小字风格（不是卡片）。

**为什么用 status 驱动而不是延迟折叠**：`message.status` 的变化天然是「Agent 做完了」的信号，不需要额外计时器。流式中展开让用户看到实时进度，完成后折叠让结论成为焦点。

**替代方案考虑**：加一个 3 秒延迟再折叠——否决，增加复杂度且用户可能在延迟期间就开始扫读。

### D3: 气泡从 message 级下放到 text 级

**决策**：

- 移除 `MessageItem` 外层 `div` 上的 `bg-card` / `shadow` / `rounded-lg` / `px-3 py-2`
- 每个 `TextPart` 自带气泡：`rounded-lg bg-card px-4 py-3 shadow-[var(--inset-hi)] border border-border/50`
- 用户消息的 `TextPart` 气泡：`bg-primary/5 border-l-2 border-primary`（保留原有左侧标识）
- 过程段无气泡、无边框、无背景
- `artifact_ref` / `deploy_status` 等结论型 part 保持各自的 Card 样式（它们本身就是卡片，不需要再套气泡）

**为什么不给每个结论 part 都套气泡**：`artifact_ref` 和 `deploy_status` 已经是 `Card` 组件，有自己的视觉边界，再套气泡会双层嵌套。只有 `text` 是纯文本需要气泡来建立视觉边界。

### D4: 过程段内部渲染去卡片化

**决策**：过程段展开时，内部的 thinking / tool_use / file_write_preview 不再渲染为 `Card`（带 border + bg-color），改为无框紧凑行：

- **thinking**：灰色斜体小字，`text-xs text-muted-foreground/70 italic`，无 border 无 bg
- **tool_use**：单行 `图标 + 名称 + 状态 + 耗时`，`text-xs text-muted-foreground`，无 Card 包裹；连续 tool_use 在同一视觉块内纵向排列，间距 `space-y-0.5`
- **file_write_preview**：保持文件名 + 状态行，但去掉 Card 边框，改用 `text-xs` 紧凑行；diff/code 预览区域保持现有样式（需要边界来显示代码）

**工具详情展开**：tool_use 的详情（参数、返回值）仍可通过点击展开，展开时用内嵌的 `bg-muted/40 rounded` 块，不用 Card。

### D5: 字体 Geist → Manrope

**决策**：

```ts
// layout.tsx
import { Manrope } from 'next/font/google'
const manrope = Manrope({
  variable: '--font-manrope',
  subsets: ['latin'],
})
```

```css
/* globals.css */
--font-sans: var(--font-manrope);
```

Mono 字体保持 Geist Mono 不变（代码块、终端输出仍需要等宽字体，Geist Mono 表现正常）。

**meta 行去 mono**：`message-item.tsx` 的 meta 行（name + time + token）从 `font-mono` 改为 `font-sans tabular-nums`，保持数字等宽对齐但去掉代码感。

### D6: 间距调整

| 位置 | 当前 | 调整后 |
|---|---|---|
| text 气泡 padding | `px-3 py-2` | `px-4 py-3` |
| Markdown 段落 | `my-1.5` | `my-2` |
| Markdown 列表项 | `space-y-0.5` | `space-y-1` |
| ProcessSegment ↔ 结论段 | `space-y-2` | `space-y-3` |
| 消息间（非分组） | `mt-4` | `mt-4`（不变） |
| 消息间（分组） | `mt-0.5` | `mt-0.5`（不变） |

### D7: 颜色微调

| 位置 | 当前 | 调整后 |
|---|---|---|
| 用户气泡 | `bg-transparent` + `border-l-2 border-primary` | `bg-primary/5` + `border-l-2 border-primary` |
| Agent 气泡 | `bg-card` | `bg-card` + `border border-border/50` |
| 工具状态色 | 硬编码 `text-blue-500` / `text-green-600` | `text-primary` / `text-success` |
| diff 行色 | 硬编码 `text-red-700` / `text-green-700` | `text-destructive` / `text-success` |

## Risks / Trade-offs

- **[过程段折叠后丢失过程可追溯性]** → 折叠摘要点击可展开，不删除信息；且 streaming 时展开保证实时可见
- **[气泡下放到 text 级后，连续多段 text 的视觉连贯性]** → 同一消息内连续多段 text 各自有气泡，中间有 `space-y-2` 间距；但这在当前数据中罕见（text part 通常不连续，中间有 tool_use 隔开）
- **[Manrope 中文回退仍是系统字体]** → 本次只换英文字体，中文回退行为不变；如果后续要优化中文可另开 change
- **[过程段聚类逻辑与现有 ToolCluster 逻辑有重叠]** → 新的 ProcessSegment 聚类是 ToolCluster 的超集（ProcessSegment 也包含连续 tool_use），实现时 ProcessSegment 取代 ToolCluster，ToolCluster 逻辑删除
