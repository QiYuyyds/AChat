## 1. 提取上传 hook

- [x] 1.1 创建 `src/hooks/use-attachment-upload.ts`，将 `MessageInput` 中的 `handleFileSelect` 函数和 `uploading` state 提取为 `useAttachmentUpload(conversationId)` hook，返回 `{ handleFiles, uploading }`
- [x] 1.2 在 `MessageInput` 中替换内联的 `uploading` state 和 `handleFileSelect` 为 hook 调用，确认 `[+]` 按钮上传和 `PendingAttachmentChip` 渲染行为不变

## 2. ChatPanel 拖拽 dropzone

- [x] 2.1 在 `ChatPanel` 组件中添加 `dragOver` state 和 `dragCounter` ref，在 `<main>` 元素上绑定 `onDragEnter` / `onDragOver` / `onDragLeave` / `onDrop` 事件
- [x] 2.2 `onDragEnter` 中检查 `e.dataTransfer.types.includes('Files')`，不包含则不激活；包含时 `dragCounter++` 并 `setDragOver(true)`
- [x] 2.3 `onDragLeave` 中 `dragCounter--`，归零时 `setDragOver(false)`
- [x] 2.4 `onDrop` 中重置 `dragCounter=0` + `setDragOver(false)`，取 `e.dataTransfer.files` 调用 `handleFiles` 上传
- [x] 2.5 调用 `useAttachmentUpload(conv.id)` 获取 `handleFiles` 和 `uploading`，将两者通过 prop 传给 `MessageInput`

## 3. 拖拽视觉反馈遮罩

- [x] 3.1 在 `ChatPanel` 的 `<main>` 内部条件渲染遮罩：`dragOver` 为 true 时渲染 `pointer-events-none` 的全屏半透明遮罩 + 中心提示卡片（图标 + "拖拽文件到此处上传" + "将添加到当前会话的附件"）
- [x] 3.2 遮罩样式：`absolute inset-0 z-40 bg-background/60 backdrop-blur-sm`，中心卡片 `border-2 border-dashed border-primary/40 bg-card/90 rounded-2xl shadow-lg`
- [x] 3.3 确认遮罩 `pointer-events-none` 不拦截 drop 事件传递到 `<main>`

## 4. MessageInput 粘贴图片

- [x] 4.1 在 `MessageInput` 的 `Textarea` 上添加 `onPaste` 处理器
- [x] 4.2 `onPaste` 中从 `e.clipboardData?.items` 筛选 `type.startsWith('image/')` 的项；无图片项时直接 return 不拦截
- [x] 4.3 有图片项时 `e.preventDefault()`，取 `getAsFile()` 调用 `handleFiles` 上传
- [x] 4.4 确认纯文本粘贴正常工作（不触发上传、文本正常写入 textarea）

## 5. uploading 状态在 MessageInput 中渲染

- [x] 5.1 `MessageInput` 接收 ChatPanel 传入的 `uploading` prop（替代原 local state），确认 `PendingAttachmentChip` 渲染逻辑正常
- [x] 5.2 确认 `uploading.length > 0` 时 slash command 的 disabled 判断仍生效（deploy / compact 命令在附件上传中不可用）

## 6. 验证

- [ ] 6.1 手动测试：从文件管理器拖拽单个文件到 ChatPanel 各区域（header / 消息列表 / 输入条），确认遮罩显示 + 上传成功 + chip 出现
- [ ] 6.2 手动测试：拖拽多个文件，确认全部上传
- [ ] 6.3 手动测试：拖拽非文件内容（选中文本），确认不触发遮罩
- [ ] 6.4 手动测试：拖拽到边界外再松手，确认遮罩消失且不上传
- [ ] 6.5 手动测试：在输入框 `Ctrl/Cmd+V` 粘贴截图，确认上传为图片附件
- [ ] 6.6 手动测试：在输入框粘贴纯文本，确认文本正常写入不上传
- [ ] 6.7 手动测试：计划审批中（composerLocked）拖拽文件和粘贴图片，确认上传正常
- [x] 6.8 运行 `pnpm typecheck` 和 `pnpm lint` 确认无错误
