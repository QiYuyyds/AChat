## 1. 图标轨导航（拆出全局 mode 切换）

- [x] 1.1 在 `sidebar.tsx` 抽出 56px 图标轨组件：6 个 mode 图标按钮 + 轨底 ⚙设置/🌗主题
- [x] 1.2 图标轨 active 态用 2px 左色条 + 主色锚定（复用 visual-system active 表达），hover 出 tooltip(label)
- [x] 1.3 移除原顶部 6 个垂直 TabButton 区块（导航已外移到图标轨）

## 2. 会话列表栏（240px）

- [x] 2.1 AChat 标题块（h1 + 副标题）原样保留，DOM 落到第二栏顶部，文案不改
- [x] 2.2 第二栏宽度 288 → 240；搜索/列表/归档区随栏迁移
- [x] 2.3 重构 `collapsed` 逻辑：图标轨常驻，collapsed 仅控制「第二栏开/合」单一维度，删除原双形态分支（含 CollapsedItem）

## 3. 副面板改右侧滑入覆盖

- [x] 3.1 `page.tsx`：副面板改 fixed overlay 后自带脱离文档流，主区 flex-1 恢复满宽，无需改 page.tsx 结构（已确认）
- [x] 3.2 `file-explorer-panel.tsx`：改 overlay 呈现（`fixed inset-y-0 right-0` + `slide-in-from-right`）；遮罩点击/Esc 关闭
- [x] 3.3 `artifact-preview-panel.tsx`：同上改 overlay 呈现 + Esc 关闭
- [x] 3.4 确认 `fileExplorerOpen`/`previewArtifactId` store 开关语义不变；右上角 header 按钮 onClick 不改
- [x] 3.5 确认文件树/产物预览互斥关系沿用现状（store 层已有互斥逻辑）
- [x] 3.6 z-index 对齐移动端抽屉段（z-30 遮罩 / z-40 面板）

## 4. 彩色用法收敛与节奏（不改调色板）

- [x] 4.1 未选中 tab/普通图标的 `text-primary`/`ring-primary` 等降级为 `muted-foreground`/中性（图标轨未选中态为 muted）
- [x] 4.2 主色仅保留在主 CTA 与当前选中态（rail active / 会话 active 左色条 / rename focus）
- [x] 4.3 `--border` 略提对比（light `0.922→0.90`、dark `10%→14%`，含 sidebar-border 同步）
- [x] 4.4 会话列表项纵向内边距 `py-2 → py-2.5`

## 5. 验证

- [x] 5.1 `pnpm typecheck` 通过
- [x] 5.2 `pnpm lint` 通过（仅 3 个既有 warning，均非本次改动文件）
- [x] 5.3 桌面端回归（截图确认）：图标轨三区布局 / 文件树从右滑入覆盖（主区不被压缩）/ 无控制台报错
- [ ] 5.4 移动端手动回归：抽屉式 sidebar 行为（窄视口下需人工确认）
- [ ] 5.5 dark 模式人工确认（已确认 light，dark 待人工核对彩色仅出现在 CTA/选中态）
