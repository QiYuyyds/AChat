# Proposal: Add Drag-Drop & Paste Attachments

## Why

当前会话附件上传只能点击输入框左侧的 `[+]` 按钮唤起文件选择器，交互路径长且不直观。用户在 IM 类应用中已习惯直接拖拽文件到对话区域或粘贴截图——这是现代聊天输入的基线体验，缺失会显得「不完整」。

## What Changes

- **整个 ChatPanel 成为文件 dropzone**：用户可以将文件拖拽到对话区域的任意位置（header / 消息列表 / 输入框），松手即上传为当前会话附件
- **拖拽视觉反馈**：拖拽文件进入 ChatPanel 时显示全屏半透明遮罩 + 中心提示卡片（「拖拽文件到此处上传」），`pointer-events-none` 确保不干扰 drop 事件传递
- **Textarea 粘贴图片**：在输入框 `Ctrl/Cmd+V` 粘贴时，拦截剪贴板中的 `image/*` 项自动上传为附件；纯文本粘贴不受影响
- **上传管道统一**：拖拽、粘贴、按钮三种入口共用同一套上传逻辑（`uploadAttachmentAPI` + `addPendingAttachment` + uploading 状态）
- **抽象 `useAttachmentUpload` hook**：将上传函数 + uploading 状态从 `MessageInput` 组件内提取为可复用 hook，供 ChatPanel（拖拽）和 MessageInput（粘贴 / 按钮）共用
- **不引入新依赖**：纯 React DnD 事件 + Clipboard API，无第三方库

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: 新增 2 个 requirement——对话区域拖拽上传附件、输入框粘贴图片上传附件

## Impact

- **前端代码**：
  - `src/components/chat-panel.tsx`：`<main>` 添加 DnD 事件 + 遮罩渲染
  - `src/components/message-input.tsx`：Textarea 添加 `onPaste`；`uploading` 状态迁移到 hook
  - `src/hooks/use-attachment-upload.ts`（新增）：封装上传函数 + uploading 状态
- **后端**：无变更（复用现有 `POST /api/conversations/:id/attachments`）
- **依赖**：无新增
- **Spec**：`openspec/specs/frontend/spec.md` 新增 2 个 requirement
