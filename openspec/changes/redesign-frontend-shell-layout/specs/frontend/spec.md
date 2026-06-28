## ADDED Requirements

### Requirement: 桌面端 shell SHALL 分为图标轨导航、会话列表、工作区主区三个区

桌面视口（`md` 及以上）下，应用外壳 MUST 由三个水平相邻的区组成：(1) 固定窄宽的**图标轨**承载全局 mode 导航（对话/产物库/Agents/分析/知识库/技能）与设置/主题入口；(2) **会话列表栏**承载当前 mode 的上下文内容（含 AChat 标题块、搜索、列表、归档区）；(3) **工作区主区**承载聊天/文件/产物内容。全局导航 MUST NOT 与上下文列表共用同一栏。

#### Scenario: 图标轨承载 mode 切换
- **WHEN** 桌面端首次加载
- **THEN** 6 个 mode 以图标按钮形式排布在固定窄宽（约 56px）的图标轨内
- **AND** 当前 mode 的图标显示 active 锚定（左色条或等价主色指示）
- **AND** hover 图标显示其文字 label 的 tooltip。

#### Scenario: AChat 标题块位于会话列表栏顶
- **WHEN** 桌面端渲染会话列表栏
- **THEN** AChat 标题与副标题渲染在该栏顶部
- **AND** 标题与副标题文案内容保持不变。

#### Scenario: 移动端保持抽屉形态
- **WHEN** 视口宽度小于 `md`
- **THEN** 侧栏仍以抽屉形式（左滑覆盖 + 遮罩）呈现
- **AND** 桌面端三区结构不影响移动端交互。

### Requirement: 副面板 SHALL 以右侧滑入覆盖形式呈现

文件树面板与产物预览面板 MUST 以从右侧滑入的 overlay 形式覆盖在工作区主区之上，而非作为常驻栏平铺挤压主区。overlay MUST 提供关闭手段（点击遮罩或 Esc），关闭后主区恢复无遮挡的单一焦点。触发开关的状态来源（`fileExplorerOpen` / `previewArtifactId`）与右上角 header 触发按钮的外观 MUST 保持不变。

#### Scenario: 打开文件树为滑入覆盖
- **WHEN** 用户点击右上角文件树按钮且 `fileExplorerOpen` 变为 true
- **THEN** 文件树面板从右侧滑入并覆盖主区
- **AND** 主聊天区宽度不被压缩。

#### Scenario: 点击遮罩关闭副面板
- **WHEN** 副面板 overlay 处于打开态且用户点击其遮罩或按下 Esc
- **THEN** 该 overlay 关闭
- **AND** 主区恢复完整可见。

#### Scenario: 右上角 header 外观不变
- **WHEN** 渲染工作区主区顶部 header
- **THEN** 其按钮集合、图标与视觉外观与变更前一致
- **AND** 仅副面板的呈现形态由平铺改为 overlay。
