## MODIFIED Requirements

### Requirement: Dispatch Child Run Message Visibility

当 `DispatchState` 存在且 `reviewStatus === 'approved'` 时，属于该 dispatch 的子任务 child run 消息 SHALL 被标记为 `hidden=true`，从聊天流中隐藏。Orchestrator 自身的消息和最终聚合消息 SHALL 保持可见。

含 `ask_user` 工具调用的子任务消息 SHALL 在 `tool_use` part 追加时翻转为 `hidden=false`，确保用户可在聊天流中看到并交互。

#### Scenario: Child run messages hidden after dispatch approval
- **WHEN** DispatchState 的 `reviewStatus` 变为 `'approved'`，子任务 child run 的 `message.start` 事件到达
- **THEN** 该消息被标记 `hidden=true`，不进入聊天流渲染

#### Scenario: ask_user message becomes visible
- **WHEN** 一条 `hidden=true` 的子任务消息追加了 `tool_use` part 且工具名为 `ask_user`
- **THEN** 该消息的 `hidden` 翻转为 `false`，在聊天流中变为可见

#### Scenario: Non-dispatch messages unaffected
- **WHEN** 不属于任何 DispatchState 的 agent 消息到达（solo 模式 / 普通群聊）
- **THEN** 消息 `hidden` 保持 `false`，正常在聊天流中渲染

#### Scenario: MessageList skips hidden messages
- **WHEN** MessageList 渲染消息列表
- **THEN** `hidden=true` 的消息不参与 `buildSegments`，不产生 wave 列或 single segment
