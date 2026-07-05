## ADDED Requirements

### Requirement: Session metadata SHALL be injected for custom_adapter runs

custom_adapter（SDK 路径）的每次 run，AgentRunner MUST 将 `language`、`timezone`、`current_time` 三项钝化会话元数据拼装进 `AdapterInput`，使模型获得语言/时区/时间感知。

#### Scenario: custom_adapter run assembles metadata

- **WHEN** AgentRunner builds `AdapterInput` for an agent whose `adapter_name` is `custom`
- **THEN** `system_prompt` contains `language` 与 `timezone` 钝化标签，且当前 user 消息尾部包含 `current_time` 钝化分桶值。

#### Scenario: CLI adapters are exempt

- **WHEN** AgentRunner builds `AdapterInput` for an agent whose `adapter_name` is `claude-code` 或 `codex`
- **THEN** 不注入任何 session metadata（CLI 适配器自管上下文）。

### Requirement: Static metadata SHALL join the cache-stable system prompt

`language` 与 `timezone` 属会话级静态字段（仅在会话建立时确定，跨轮次不变），MUST 注入 `system_prompt` 以加入 OpenAI 兼容协议的隐式前缀缓存稳定区，MUST NOT 注入动态 user 消息。

#### Scenario: static fields appear in system prompt

- **WHEN** 一轮 custom_adapter run 完成 prompt 拼装
- **THEN** `language`（如 `zh-CN`）与 `timezone`（如 `GMT+8`）出现在 `system_prompt` 中，且不出现在尾部 user 消息的动态区。

#### Scenario: static fields remain stable across turns

- **WHEN** 同一会话连续进行多轮 custom_adapter run
- **THEN** `system_prompt` 中的 `language`/`timezone` 值跨轮不变（除非会话级配置变更）。

### Requirement: Dynamic metadata SHALL be mounted at the prompt tail

`current_time` 为高频变动字段，MUST 钝化后注入当前 user 消息尾部，MUST NOT 注入 `system_prompt`，以避免每轮击穿缓存前缀。

#### Scenario: current_time placed in user message tail

- **WHEN** AgentRunner 拼装当前轮 user 消息
- **THEN** `current_time` 的钝化分桶值出现在 user 消息中，且位于动态 `<system-reminder>` 块与用户真实输入之间或之后的尾部位置。

#### Scenario: current_time does not pollute system prompt

- **WHEN** 跨轮观察 `system_prompt` 内容
- **THEN** `system_prompt` 不含 `current_time` 或其分桶值（system_prompt 跨轮稳定）。

### Requirement: Metadata values SHALL be blunted to coarse-grained tags

原始高频数据 MUST 钝化为块状标签后方可注入：`current_time` MUST 降频为 time_bucket（如 `Sunday_Morning`、`Late_Night`），MUST NOT 保留秒级/分钟级精度；`language` MUST 为 locale 标签（如 `zh-CN`）；`timezone` MUST 为偏移标签（如 `GMT+8`）。

#### Scenario: exact timestamp is bucketed

- **WHEN** 当前真实时间为 `2026-07-05 11:14:58`
- **THEN** 注入 prompt 的 `current_time` 形如 `Sunday_Morning`（按上下午/星期分桶），不含 `11:14:58` 原值。

#### Scenario: language is a locale tag

- **WHEN** 会话语言为中文
- **THEN** 注入值为 `zh-CN` 形式的 locale 标签，而非自然语言句子。

### Requirement: Session metadata SHALL NOT be persisted

session metadata 为运行时计算值（来自请求环境/会话配置），MUST NOT 写入数据库，MUST NOT 新增 DB 列或表。

#### Scenario: no DB schema change for metadata

- **WHEN** 变更实施完成
- **THEN** 不存在存储 `language`/`timezone`/`current_time` 的数据库列；metadata 仅存在于拼装后的 prompt 字符串中。
