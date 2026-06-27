## 1. Token 重写（globals.css）

- [x] 1.1 将 `:root` 中性色（`--background`/`--foreground`/`--card`/`--secondary`/`--muted`/`--accent`/`--border`/`--input`）改为 hue 250°、chroma 0.005–0.015 的冷调 oklch 值
- [x] 1.2 将 `.dark` 中性色改为冷调深石墨（`--background` 量级 `oklch(0.16 0.012 255)`）
- [x] 1.3 将 `--primary`（light）调到 `oklch(0.56 0.21 265)` 量级，`.dark` 的 `--primary` 同步到 `oklch(0.62 0.20 265)` 量级，保持 hue 263–266
- [x] 1.4 将 `--radius` 从 `0.625rem` 改为 `0.375rem`
- [x] 1.5 新增 `--success`（`oklch(0.60 0.16 145)`）与 `--warning`（`oklch(0.72 0.15 75)`）语义 token，并在 `.dark` 提供对应值
- [x] 1.6 新增 `--shadow-sm`、`--shadow-md`、`--inset-hi` 三个阴影 token，light/dark 各自给出可见度合适的值
- [x] 1.7 在 `@theme inline` 注册 `--color-success`/`--color-warning`/`--shadow-sm`/`--shadow-md`/`--inset-hi` 的 Tailwind 映射，使 `bg-success`/`text-warning`/`shadow-md` 等类可用
- [x] 1.8 验证：light/dark 切换无控制台报错，吃 token 的 shadcn 基元（button/input/card）视觉自动联动

## 2. 基元层去描边走层次

- [x] 2.1 `ui/card`：移除 `ring-1 ring-foreground/10`，改用 `shadow-md` + `--inset-hi`（通过 `style` 或 Tailwind arbitrary 引用）
- [x] 2.2 `ui/dialog` 的 `DialogContent`：移除 `ring-1 ring-foreground/10`，改用 `shadow-md` + `--inset-hi`
- [x] 2.3 验证：card 与 dialog 弹层在 light/dark 下层次正常，无描边残留

## 3. 业务组件硬编码色收敛

- [x] 3.1 `sidebar.tsx`：删除确认按钮 `bg-red-600 hover:bg-red-700` → `bg-destructive hover:bg-destructive/90`
- [x] 3.2 `message-item.tsx`：撤回按钮 `bg-red-600` → `bg-destructive`；error 态 `border-red-300 bg-red-50/40` → `border-destructive/40 bg-destructive/10`；aborted 态 `border-zinc-300 bg-zinc-50/60` → `border-muted-foreground/40 bg-muted/60`
- [x] 3.3 `artifact-library.tsx`：`bg-red-600`/`hover:text-red-600`/`text-green-600`/`text-red-600` → `bg-destructive`/`hover:text-destructive`/`text-success`/`text-destructive`
- [x] 3.4 `skill-library.tsx`：`border-red-500/30 bg-red-50/30 text-red-700` → `border-destructive/30 bg-destructive/10 text-destructive`；`hover:text-red-600`/`bg-red-600` → `hover:text-destructive`/`bg-destructive`
- [x] 3.5 `document-detail.tsx`：`hover:text-red-600`/`bg-red-600` → `hover:text-destructive`/`bg-destructive`
- [x] 3.6 `create-agent-dialog.tsx`：`border-red-200 bg-red-50 text-red-800` → `border-destructive/30 bg-destructive/10 text-destructive`；`text-red-500`（必填星标）→ `text-destructive`
- [x] 3.7 `new-conversation-dialog.tsx`：green 状态类 → `success` 映射；amber 状态类 → `warning` 映射（border/bg/text 三件套）
- [x] 3.8 `upload-document-dialog.tsx`：green 成功态 → `success` 映射；amber 提示态 → `warning` 映射；red 错误态 → `destructive` 映射
- [x] 3.9 `chat-panel.tsx`：`text-[#3370FF]` → `text-primary`
- [x] 3.10 验证：对 `src/components/` grep 匹配 `bg-(red|green|amber)-` 与 `text-\[#`，命中数为 0

## 4. 结构性视觉表达

- [x] 4.1 `message-item`：气泡容器移除 `border`，改用 `bg-card` + `--inset-hi` 内凹高光
- [x] 4.2 `message-item`：用户消息 `bg-primary/5 border-primary/20` → `border-l-2 border-primary`（左色条）+ 透明底
- [x] 4.3 `message-item`：meta 行（发送者名/时间/token 计数/streaming loader）加 `font-mono`
- [x] 4.4 `sidebar`：`ConversationItem` active 态 `bg-accent` → `border-l-2 border-primary` + 透明底
- [x] 4.5 `sidebar`：`TabButton` active 态 `bg-primary text-primary-foreground` → 底部 2px 主色线 + `text-primary` 文字提色
- [x] 4.6 `chat-panel`：`TabButton` active 态 `border-primary/30 bg-background` → 底部 2px 主色线 + 文字提色；tab bar 文字加 `font-mono`
- [x] 4.7 验证：消息气泡、sidebar、tab 在 light/dark 下气质成型，active 态识别清晰

## 5. 端到端验收

- [x] 5.1 确认 `apps/mobile/src/styles/tokens.css` 内容未被修改，移动端仍使用 `#f2f2f7` 作为 `--bg`
- [x] 5.2 确认无功能/数据/路由回归：会话收发、Agent 协作、产物预览、文件树均正常
- [x] 5.3 `pnpm typecheck` 通过
- [x] 5.4 `pnpm lint` 通过
