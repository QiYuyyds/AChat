## ADDED Requirements

### Requirement: 中性色 SHALL 采用冷调低 chroma 而非无色相纯灰

桌面端 `:root` 与 `.dark` 中所有中性 token（`--background`、`--foreground`、`--card`、`--secondary`、`--muted`、`--accent`、`--border`、`--input`）MUST 使用 hue 约 250°、chroma 在 0.005–0.015 之间的 oklch 值，禁止使用 chroma 为 0 的纯灰。主色 `--primary` MUST 为 `oklch(0.56 0.21 265)` 量级（电光靛），保持与原字节蓝同 hue 族。

#### Scenario: Light 模式背景为冷调灰白
- **WHEN** 主题为 light
- **THEN** `--background` 解析为 `oklch(0.99 0.008 250)` 量级
- **AND** chroma 大于 0 且不为 0。

#### Scenario: Dark 模式背景为冷调深石墨
- **WHEN** 主题为 dark
- **THEN** `--background` 解析为 `oklch(0.16 0.012 255)` 量级
- **AND** chroma 大于 0 且不为 0。

#### Scenario: 主色保持字节蓝 hue 族但更深冷
- **WHEN** 读取 `--primary`
- **THEN** 其 oklch hue 在 263–266 区间
- **AND** 亮度不高于 0.58。

### Requirement: 状态色 SHALL 归一到语义 token 且禁止硬编码

新增 `--success`、`--warning` 语义 token；`--destructive` 沿用。所有业务组件 MUST 通过语义 token 或其 Tailwind 映射类（如 `bg-destructive`、`text-success`、`bg-warning/10`）引用状态色，禁止在 `src/components/**/*.tsx` 中硬编码 `bg-red-*`、`bg-green-*`、`bg-amber-*`、`text-[#3370FF]` 等 Tailwind 调色板类或 hex 字面量。

#### Scenario: 危险操作按钮使用 destructive token
- **WHEN** 删除/撤回按钮渲染
- **THEN** 其 className 引用 `bg-destructive` 或 `--destructive` 映射
- **AND** 不包含 `bg-red-600` 等硬编码类。

#### Scenario: 成功状态使用 success token
- **WHEN** 文档/产物状态显示为成功
- **THEN** 其样式引用 `--success` 映射类
- **AND** 不包含 `bg-green-50`、`text-green-600` 等硬编码类。

#### Scenario: 警告状态使用 warning token
- **WHEN** 本地工作目录标记或提示渲染
- **THEN** 其样式引用 `--warning` 映射类
- **AND** 不包含 `bg-amber-50`、`text-amber-700` 等硬编码类。

#### Scenario: 全局无残留硬编码状态色
- **WHEN** 对 `src/components/` 执行 grep 匹配 `bg-(red|green|amber)-` 与 `text-\[#`
- **THEN** 命中数为 0。

### Requirement: 圆角基准 SHALL 收紧到 0.375rem

`--radius` MUST 设为 `0.375rem`（6px）。`--radius-sm`/`--radius-md`/`--radius-lg` 等派生刻度 MUST 基于此基准按现有比例计算。shadcn 基元（button、input、card、dialog）的圆角 MUST 随 token 自动收紧，不新增独立圆角覆盖。

#### Scenario: 基准圆角为 6px
- **WHEN** 读取 `:root` 的 `--radius`
- **THEN** 其值为 `0.375rem`。

#### Scenario: 控件圆角随基准收紧
- **WHEN** button/input 渲染
- **THEN** 其圆角由 `--radius-*` 派生
- **AND** 视觉上不大于 6px 基准的对应比例。

### Requirement: 表面层次 SHALL 通过背景差异与阴影表达而非描边

`ui/card` 与 `ui/dialog` MUST 移除 `ring-1`/`border` 描边，改用 `--shadow-md`（或等价阴影）+ `--inset-hi` 内凹高光制造层次。`--card` 的亮度 MUST 与 `--background` 保持 oklch 0.01–0.015 的亮度差，以保证面板边界可辨识。新增 `--shadow-sm`、`--shadow-md`、`--inset-hi` 三个阴影 token。

#### Scenario: Card 无描边有阴影
- **WHEN** `ui/card` 渲染
- **THEN** 其 className 不含 `ring-1` 或 `border-border`
- **AND** 应用 `--shadow-md` 与 `--inset-hi`。

#### Scenario: Dialog 弹层用阴影而非描边
- **WHEN** `ui/dialog` 弹层渲染
- **THEN** 其 className 不含 `ring-1`
- **AND** 应用 `--shadow-md`。

#### Scenario: 面板与背景存在可辨识亮度差
- **WHEN** light 模式下对比 `--card` 与 `--background`
- **THEN** 两者 oklch 亮度差在 0.01–0.015 区间。

### Requirement: 列表与 Tab 的 active 态 SHALL 用左色条锚定而非色块填充

`sidebar` 会话列表 active 态 MUST 使用 2px 主色左色条（`border-l-2 border-primary`）+ 透明或极淡背景，禁止使用 `bg-accent`/`bg-primary` 整块填充。sidebar Tab 与 `chat-panel` 文件 tab 的 active 态 MUST 使用底部 2px 主色线 + 文字提色，禁止使用 `bg-primary` 填充。

#### Scenario: active 会话用左色条
- **WHEN** 会话项处于 active
- **THEN** 其左侧呈现 2px 主色色条
- **AND** 背景不为 `bg-accent` 实色填充。

#### Scenario: active Tab 用底线而非填充
- **WHEN** sidebar Tab 或 chat 文件 tab 处于 active
- **THEN** 其底部呈现 2px 主色线
- **AND** 背景不为 `bg-primary` 填充。

### Requirement: 消息气泡 SHALL 用背景层次与左色条表达而非描边与淡底

`message-item` 的气泡容器 MUST 移除 `border`，改用 `bg-card`（略亮于背景）+ `--inset-hi` 内凹高光表达层次。用户消息 MUST 使用 2px 主色左色条（`border-l-2 border-primary`）代替 `bg-primary/5` 淡蓝底。消息 meta 行（发送者名、时间、token 计数、status）MUST 使用 `font-mono` 等宽字体。

#### Scenario: 气泡无描边
- **WHEN** Agent 或用户消息气泡渲染
- **THEN** 其 className 不含 `border` 描边类
- **AND** 应用 `bg-card` 与 `--inset-hi`。

#### Scenario: 用户消息用左色条而非淡底
- **WHEN** 用户消息气泡渲染
- **THEN** 其左侧呈现 2px 主色色条
- **AND** 不应用 `bg-primary/5` 淡底。

#### Scenario: meta 行使用等宽字体
- **WHEN** 消息 meta 行（时间、token 计数）渲染
- **THEN** 其字体为 `font-mono`。

### Requirement: 视觉 token 体系 SHALL 不覆盖移动端

本 capability 的所有 token 与规则 MUST 仅作用于桌面端 `src/`。`apps/mobile/` MUST 继续使用其独立 iOS 风格 token（`#f2f2f7` 分组背景、glass blur、iOS 系统色），本变更不得修改 `apps/mobile/src/styles/tokens.css` 及其组件样式。

#### Scenario: 移动端 token 文件不变
- **WHEN** 本变更实施完成
- **THEN** `apps/mobile/src/styles/tokens.css` 的内容与变更前一致
- **AND** 移动端仍使用 `#f2f2f7` 作为 `--bg`。
