## ADDED Requirements

### Requirement: DAG Node Click → Task Detail Panel

DAG 图在只读模式（`editable=false`）下，节点 SHALL 可点击。点击节点时 SHALL 触发 `onTaskSelect(taskId)` 回调，由父组件决定如何展示详情。

#### Scenario: Click node in read-only mode
- **WHEN** 用户点击只读 DAG 图中的某个任务节点
- **THEN** `onTaskSelect` 回调被调用，参数为该节点的 `taskId`

#### Scenario: Click node in editable mode
- **WHEN** 用户在编辑模式下点击节点
- **THEN** 不触发 `onTaskSelect`（编辑模式下双击进入编辑，单击不触发详情）

### Requirement: Task Detail Panel

系统 SHALL 提供右侧 `TaskDetailPanel` 组件，当 `selectedTaskId` 非空时展示该任务的执行细节。

#### Scenario: Panel opens on node click
- **WHEN** `selectedTaskId` 被设置为某个 dispatch 任务的 id
- **THEN** 右侧面板展示：任务 Agent 头像+名称、任务 ID、任务状态图标、任务描述、TurnTimeline（如有 turnMetrics）、该子任务 child run 的所有消息 parts

#### Scenario: Panel content updates in real-time
- **WHEN** 子任务正在执行，新的 message parts 持续流入
- **THEN** 详情面板中的消息列表实时更新，无需手动刷新

#### Scenario: Panel closes on close button
- **WHEN** 用户点击详情面板的关闭按钮
- **THEN** `selectedTaskId` 被清空为 `null`，面板消失

#### Scenario: Panel switches on different node click
- **WHEN** 面板已展示任务 A 的详情，用户点击任务 B 的节点
- **THEN** 面板切换为展示任务 B 的详情

### Requirement: Selected Node Visual Feedback

DAG 图中当前被选中的节点 SHALL 有视觉高亮（边框 `ring-2 ring-primary`），与未选中节点区分。

#### Scenario: Node highlighted when selected
- **WHEN** `selectedTaskId` 等于某节点的 `taskId`
- **THEN** 该节点渲染时添加 `ring-2 ring-primary` 样式

#### Scenario: Node highlight removed when deselected
- **WHEN** `selectedTaskId` 被清空或切换到其他节点
- **THEN** 之前选中的节点移除高亮样式
