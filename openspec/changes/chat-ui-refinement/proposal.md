# Chat UI Refinement

## Why

当前对话界面在长对话场景下可读性差：Agent 回复中的工具调用（thinking、tool_use、file_write_preview）与文字结论（text）视觉权重相同，全部以独立卡片平铺，用户扫读时无法快速定位「Agent 到底说了什么」。同时英文字体 Geist 偏冷，与暖灰米主题不搭；消息气泡内部间距偏紧，缺乏呼吸感。

## What Changes

- **字体替换**：Geist → Manrope（英文），更贴合暖灰米主题的温暖气质；meta 信息行去掉 `font-mono`，改用 `font-sans tabular-nums` 保持数字等宽但不显代码感
- **PartList 聚类重写**：将 message parts 重新分类为「过程段」（thinking / tool_use / file_write_preview）与「结论段」（text / artifact_ref / deploy_status / execution_plan / deploy_candidates），过程段在原始位置就地折叠，结论段保留气泡
- **气泡层级重构**：移除 message 级外层大气泡，改为每个 text part 自带小气泡（`bg-card` + padding），过程段无气泡无边框，形成「结论重 / 过程轻」的视觉对比
- **过程段自动折叠**：`message.status === 'streaming'` 时过程段展开显示（让用户看到 Agent 在做什么）；`message.status` 变为终态（complete / error / aborted / interrupted）后，过程段自动折叠为一行摘要（如「▸ 思考 3.2s · 2 个工具 · 2.0s」），点击可展开
- **间距微调**：气泡 padding 从 `px-3 py-2` 增至 `px-4 py-3`；段落间距 `my-1.5` → `my-2`；过程段与结论段间距增大
- **颜色微调（暖灰米主题不变）**：用户气泡从 `bg-transparent` 改为 `bg-primary/5` 增强区分度；Agent 气泡加 `border-border/50` 增强轮廓；硬编码的 `blue-500` / `green-600` / `red-700` 统一换为 `--primary` / `--success` / `--destructive` token

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: 消息渲染从「外层大气泡 + parts 平铺」改为「过程段/结论段交替 + 结论气泡化 + 过程段自动折叠」；字体从 Geist 换为 Manrope

## Impact

- `src/app/layout.tsx`：字体加载 Geist → Manrope
- `src/app/globals.css`：`--font-sans` 变量替换；用户/Agent 气泡色值微调
- `src/components/message-item.tsx`：移除外层大气泡结构；meta 行去 mono；间距调整
- `src/components/message-parts.tsx`：`PartList` 聚类逻辑重写（过程段/结论段）；`TextPart` 加气泡；`ThinkingPart` / `ToolUsePart` / `FileWritePreviewPart` 去卡片化；新增 `ProcessSegment` 折叠组件
- `src/components/markdown.tsx`：段落间距微调
- `src/components/message-parts.test.tsx`：聚类逻辑测试更新
- 不涉及后端、DB schema、事件协议、数据结构变更
