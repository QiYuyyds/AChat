## 1. 字体替换

- [x] 1.1 `layout.tsx`：Geist → Manrope（`import { Manrope } from 'next/font/google'`，`variable: '--font-manrope'`，`subsets: ['latin']`），html className 变量名同步更新
- [x] 1.2 `globals.css`：`--font-sans` 从 `var(--font-geist-sans)` 改为 `var(--font-manrope)`；`--font-mono` 保持 `var(--font-geist-mono)` 不变
- [x] 1.3 `message-item.tsx`：meta 行（line 210 附近）`font-mono` → `font-sans tabular-nums`

## 2. PartList 双段聚类重写

- [x] 2.1 在 `message-parts.tsx` 中定义 part 类型分类常量：`PROCESS_PART_TYPES`（thinking / tool_use / file_write_preview）和 `CONCLUSION_PART_TYPES`（其余）
- [x] 2.2 重写 `PartList` 聚类算法：线性遍历 parts，连续过程型 part 归入 `ProcessSegment` 缓冲区，遇到结论型 part 先 flush 缓冲区再渲染结论 part，末尾再 flush 一次
- [x] 2.3 删除旧 `ToolCluster` 组件及其调用（`ProcessSegment` 是其超集）
- [x] 2.4 新增 `ProcessSegment` 组件：接收过程型 parts 数组 + `messageStatus`，根据 status 决定展开/折叠

## 3. ProcessSegment 折叠/展开

- [x] 3.1 `ProcessSegment` 组件：`messageStatus === 'streaming'` 时默认展开；非 streaming 时默认折叠为一行摘要
- [x] 3.2 折叠摘要计算：遍历 segment 内 parts 统计 thinking 耗时、工具数量、总耗时，按 design.md D2 的格式生成摘要文本（`▸ 思考 Xs · N 个工具 · Xs` / `▸ N 个工具 · Xs` / `▸ 已深度思考 Xs`）
- [x] 3.3 用户点击折叠摘要可手动切换展开/折叠（`useState` 管理 `userOpened`，与 status 驱动的默认值组合：streaming 时默认展开但用户可手动折叠；非 streaming 时默认折叠但用户可手动展开）
- [x] 3.4 `tool_result` 仍按 `callId` 提前匹配到对应 `tool_use`（沿用现有 `resultByCallId` 逻辑）

## 4. 过程段内部去卡片化

- [x] 4.1 `ThinkingPart`：streaming 和展开态都改为无 border / 无 bg 的灰色斜体小字（`text-xs text-muted-foreground/70 italic`），移除现有的 `border-dashed border-muted-foreground/30 bg-muted/40` 样式
- [x] 4.2 `ToolUsePart`：移除 `Card` 包裹，改为单行摘要（`icon + name + status + duration`，`text-xs text-muted-foreground`）；连续 tool_use 在 `ProcessSegment` 内纵向排列 `space-y-0.5`
- [x] 4.3 `ToolUsePart` 详情展开：点击后参数/返回值用内嵌 `bg-muted/40 rounded` 块渲染，不用 `Card`；命令预览和 bash 输出预览保持现有 `TerminalPreviewBlock` 样式（它们需要边界来显示终端输出）
- [x] 4.4 `FileWritePreviewPart`：移除 `Card` 外框，文件名 + 状态行改为 `text-xs` 紧凑行；diff/code 预览区域保持现有 `DiffBlock` / `CodeBlock` 样式
- [x] 4.5 `CompactDiffPreview`：硬编码的 `text-red-700` / `text-green-700` → `text-destructive` / `text-success`

## 5. 气泡层级重构

- [x] 5.1 `message-item.tsx`：移除外层 content div 上的 `bg-card px-3 py-2 shadow-[var(--inset-hi)] rounded-lg`（line 252-260 附近），改为透明容器
- [x] 5.2 `message-item.tsx`：用户消息的 `border-l-2 border-primary bg-transparent` 也从外层移除，下放到 TextPart 气泡
- [x] 5.3 `TextPart`（`message-parts.tsx`）：包裹一层气泡 div，接收 `isUser` prop 决定样式（agent: `bg-card border border-border/50`；user: `bg-primary/5 border-l-2 border-primary`），padding `px-4 py-3`
- [x] 5.4 `PartList`：向 `TextPart` 传递 `isUser` prop（从 `message.role` 获取，需 PartList 接收 `messageRole` 参数或从 `messageStatus` 旁边传入）
- [x] 5.5 `message-item.tsx`：error / aborted / interrupted 状态的边框样式从外层下放到 TextPart 气泡（error: `border-destructive/40 bg-destructive/10`；aborted/interrupted: `border-muted-foreground/40`）

## 6. 间距与颜色微调

- [x] 6.1 `markdown.tsx`：段落 `my-1.5` → `my-2`；列表项 `space-y-0.5` → `space-y-1`
- [x] 6.2 `message-parts.tsx`：`PartList` 容器 `space-y-2` → `space-y-3`（过程段与结论段间距增大）
- [x] 6.3 `message-parts.tsx`：`ExecutionPlanPart` 硬编码 `text-blue-500` / `bg-blue-500` → `text-primary` / `bg-primary`；`text-green-600` → `text-success`
- [x] 6.4 `message-parts.tsx`：`FileWritePreviewPart` 硬编码 `text-green-600` → `text-success`
- [x] 6.5 `globals.css`：dark mode `--border` 透明度从 `12%` 微调到 `15%`（增强暗色下分隔线可见度）

## 7. 测试更新

- [x] 7.1 更新 `message-parts.test.tsx`：为新的 `ProcessSegment` 聚类逻辑添加测试用例（过程型+结论型交替、纯过程型、纯结论型、连续 tool_use 合并）
- [x] 7.2 更新 `message-parts.test.tsx`：测试 `ProcessSegment` 折叠/展开行为（streaming 默认展开、complete 默认折叠、用户手动切换）
- [x] 7.3 运行 `pnpm typecheck` 和 `pnpm lint` 确认无类型/lint 错误
