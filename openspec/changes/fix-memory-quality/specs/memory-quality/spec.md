## ADDED Requirements

### Requirement: Preference 写入 SHALL 校验并截断超长 value

`Preference.set(key, value)` 在写入前 MUST 检查 value 长度。超过 200 字符的 value MUST 被截断为前 197 字符 + "..."。空 key 或 None value 的行为不变（静默忽略）。

#### Scenario: 正常偏好写入不受影响
- **WHEN** 用户说"我喜欢清新风格"，提取出 key="喜好" value="清新风格"
- **THEN** Preference 表存入 "喜好: 清新风格"（未超 200 字符，原样写入）

#### Scenario: 超长对话片段被截断
- **WHEN** 用户说"我喜欢唱跳rap，而且..."（后续跟了 2000+ 字符），提取出 value 超过 200 字符
- **THEN** Preference 表存入的 value 为前 197 字符 + "..."（总长 200 字符）

#### Scenario: 空 key 不写入
- **WHEN** `preference.set("", "some value")` 被调用
- **THEN** 不执行任何写入（行为不变）

### Requirement: extract_memory_from_reply SHALL 同时写入偏好仓和 LTM

`memory_writer.extract_memory_from_reply` 在遍历 LLM 提取的 k-v pairs 时，MUST 对每个 pair 同时执行 `preference.set(k, v)` 和 `ltm.store_classified()`，对齐原版双写逻辑。

#### Scenario: LLM 提取的偏好同时进入两个存储
- **WHEN** assistant 回复被 LLM 提取出 `{"视觉风格": "editorial/magazine"}`
- **THEN** Preference 表存入 "视觉风格: editorial/magazine"，且 LTM 存入 "用户视觉风格: editorial/magazine"（分类为 preference）

#### Scenario: preference.set 失败不阻断 LTM 写入
- **WHEN** `preference.set(k, v)` 抛出异常
- **THEN** 异常被捕获并 log warning，`ltm.store_classified()` 仍正常执行

### Requirement: importance SHALL 按 category 分级

`memory_writer.extract_memory_from_reply` 中的 importance MUST 根据分类结果赋值，不再硬编码 0.7。分级表：

| category | importance |
|---|---|
| identity | 0.9 |
| policy | 0.8 |
| preference | 0.7 |
| fact | 0.5 |
| episodic | 0.4 |
| tool_failure | 0.3 |
| general | 0.3 |

未命中任何 category 时 MUST 使用 0.3（与 general 一致）。

#### Scenario: 身份信息获得高优先级
- **WHEN** LLM 提取出 `{"名称": "涵涵"}`，分类为 identity
- **THEN** importance = 0.9（而非硬编码的 0.7）

#### Scenario: 通用事实获得低优先级
- **WHEN** LLM 提取出 `{"天气": "今天晴天"}`，分类为 general
- **THEN** importance = 0.3

### Requirement: fact_content SHALL 去除双重"用户"前缀

`memory_writer.extract_memory_from_reply` 拼接 `fact_content` 前，MUST 检测 key 是否已含"用户"前缀。若已含，MUST 去除后再拼接，避免产生"用户用户名称"。

#### Scenario: LLM 返回已含前缀的 key
- **WHEN** LLM 提取出 `{"用户名称": "涵涵"}`
- **THEN** fact_content = "用户名称: 涵涵"（而非"用户用户名称: 涵涵"）

#### Scenario: LLM 返回不含前缀的 key
- **WHEN** LLM 提取出 `{"字体": "Noto Serif SC"}`
- **THEN** fact_content = "用户字体: Noto Serif SC"（正常拼接）

### Requirement: 用户消息 MUST 触发 LLM 偏好提取覆盖

`memory_service.on_message_end(role="user")` MUST 在规则提取之后，异步调用 LLM 偏好提取（`llm.extract_preferences`）。LLM 提取的结果通过 `preference.save_batch()` 覆盖规则提取的值。LLM 提取失败时 MUST 静默降级，保留规则提取结果。

#### Scenario: LLM 提取覆盖规则提取的粗糙值
- **WHEN** 规则提取出 "喜好: 唱跳rap而且回忆里提到很多..."（截断到 200 字符），LLM 提取出 `{"喜好": "唱跳rap"}`
- **THEN** Preference 表最终值为 "喜好: 唱跳rap"（LLM 结果覆盖规则结果）

#### Scenario: LLM 不可用时静默降级
- **WHEN** `_generate_fn` 为 None 或 LLM 调用失败
- **THEN** Preference 表保留规则提取的值，不抛异常

### Requirement: LLM 偏好提取方法 extract_preferences

系统 MUST 提供 `extract_preferences(generate_fn, msg)` 方法（位于 `memory_writer.py`），用 LLM 从用户消息中提取偏好 JSON。LLM 调用失败时 MUST fallback 到规则提取（`_extract_rule_based`）。

#### Scenario: LLM 成功提取偏好
- **WHEN** 用户说"我叫涵涵，我喜欢清新沉浸感的设计风格"
- **THEN** LLM 返回 `{"姓名": "涵涵", "喜好": "清新沉浸感的设计风格"}`

#### Scenario: LLM 返回非法 JSON 时 fallback 到规则
- **WHEN** LLM 返回 "这不是JSON"
- **THEN** fallback 到 `_extract_rule_based(msg)` 返回规则提取结果

#### Scenario: 非 LLM 模式直接走规则
- **WHEN** `generate_fn` 为 None
- **THEN** 直接调用 `_extract_rule_based(msg)` 返回结果
