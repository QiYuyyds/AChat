# Design: Drag-Drop & Paste Attachments

## Context

`MessageInput` 组件当前通过隐藏的 `<input type="file" multiple>` + `[+]` 按钮点击来上传附件。`handleFileSelect(files: FileList)` 已经封装了上传管道：遍历 FileList → `uploadAttachmentAPI(conversationId, file)` → `addPendingAttachment` → 维护 `uploading` local state 显示 `PendingAttachmentChip`。

项目内已有两处成熟的拖拽实现（`skill-library.tsx`、`upload-document-dialog.tsx`），均采用 `onDragOver/onDragLeave/onDrop` + `dragOver` state + `border-dashed` 视觉反馈模式。本变更将拖拽引入对话主界面，同时增加粘贴图片支持。

## Goals / Non-Goals

**Goals:**

- 用户可将文件拖拽到 ChatPanel 的任意位置（header / 消息列表 / 输入框）完成上传
- 用户可在输入框 `Ctrl/Cmd+V` 粘贴图片自动上传为附件
- 拖拽时有清晰的全屏视觉反馈（遮罩 + 中心提示卡片）
- 拖拽、粘贴、按钮三种入口共用同一套上传逻辑
- 拖拽和粘贴在 `composerLocked`（计划审批中）时仍可工作——附件上传与审批独立

**Non-Goals:**

- 不支持文件夹拖入（消息附件场景不需要递归收集目录树）
- 不改动后端附件上传 API（`POST /api/conversations/:id/attachments` 不变）
- 不在 GuideFloatingPanel 中加拖拽 / 粘贴（guide 会话不支持附件）
- 不引入第三方 DnD 库（原生 HTML5 Drag and Drop 足够）

## Decisions

### Decision 1: 整个 ChatPanel `<main>` 作为 dropzone，而非仅输入条

**选择**：在 `ChatPanel` 的 `<main>` 上绑定 DnD 事件。

**理由**：输入条胶囊高度仅 40px，拖拽目标太窄。ChatGPT 和 Claude 均采用全对话区域 dropzone，用户拖哪都行。

**替代方案**：仅输入条加 DnD → 目标区域太窄，体验差；输入条 + 上方 80px 虚拟区域 → 改动局限在 MessageInput 内但逻辑别扭，且消息列表滚动时虚拟区域定位困难。

### Decision 2: dragCounter 防抖处理嵌套 enter/leave

**选择**：用 ref 维护 `dragCounter`，`dragenter` 时 +1，`dragleave` 时 -1，归零时清除 `dragOver`。

**理由**：浏览器在拖入子元素时父容器会收到 `dragleave`，直接用 boolean 会导致遮罩闪烁。这是标准解法。

### Decision 3: 遮罩 `pointer-events-none`，drop 事件绑在 `<main>` 上

**选择**：遮罩 div 设 `pointer-events-none`，`onDrop` 绑在 `<main>` 上。

**理由**：遮罩如果拦截事件，`<main>` 的 `onDrop` 收不到 drop。`pointer-events-none` 让事件穿透到下层 DOM，最终冒泡到 `<main>`。

### Decision 4: 只响应 `Files` 类型的拖拽

**选择**：`onDragEnter` 中检查 `e.dataTransfer.types.includes('Files')`，不包含则不激活遮罩。

**理由**：避免拖选中文本、拖拽内部 DOM 元素时误触发附件上传遮罩。

### Decision 5: 提取 `useAttachmentUpload(conversationId)` hook

**选择**：将 `handleFileSelect` + `uploading` state 从 MessageInput 提取为 `useAttachmentUpload` hook，返回 `{ handleFiles, uploading }`。

**理由**：
- 项目已有两处手写 DnD 上传（skill-library、upload-document-dialog），加上本变更是第三处——按 CLAUDE.md「三处重复才提抽象」满足条件
- ChatPanel（拖拽）和 MessageInput（粘贴 / 按钮）需要共享 uploading 状态和上传函数，hook 是最自然的共享方式
- hook 返回的 `uploading` 是数组（`{ tempId, name }[]`），与当前 MessageInput 的 `uploading` state 结构一致，`PendingAttachmentChip` 渲染逻辑不变

**替代方案**：将 uploading 提到 Zustand store → 改动 store 结构，且 uploading 是临时 UI 状态（上传完就消失），不适合放 store。

### Decision 6: 粘贴只拦截 `image/*`，纯文本不干预

**选择**：`onPaste` 中检查 `clipboardData.items`，筛选 `type.startsWith('image/')`。有图片时 `preventDefault()` 阻止默认粘贴（避免图片被同时粘进 textarea 成为乱码），取 `getAsFile()` 上传。无图片时直接 return，让 textarea 正常接收文本。

**理由**：粘贴图片是最常见的场景（截图工具直接粘贴）。混合内容（文本+图片）时阻止默认行为，避免图片 blob 被粘成乱码文本。

## Risks / Trade-offs

- **[遮罩闪烁]** 嵌套 DOM 的 `dragleave` 误触发 → `dragCounter` ref 防抖，归零才清除
- **[非文件拖拽误触发]** 拖选中文本 / 内部元素时误显示遮罩 → `types.includes('Files')` 守卫
- **[粘贴大文件]** 用户粘贴超大截图 → 后端附件大小限制兜底，前端不做额外检查（与按钮上传行为一致）
- **[uploading 状态跨组件]** ChatPanel 和 MessageInput 各自调用 hook 会得到独立 state → ChatPanel 调用 hook 获取 `handleFiles`，通过 prop 传给 MessageInput；MessageInput 内部不再单独调用 hook，而是复用 ChatPanel 传入的 `handleFiles` 和 `uploading`
