# Spec Delta: memory-pipeline

## MODIFIED Requirements

### Requirement: Dream Extract Quality Gate

dream_extract 的 system prompt SHALL 包含以下质量指导：
- "Prefer fewer, richer units over exhaustive file summaries" — 反摘要指导
- "This extraction step is the gate for not worth memorizing" — 质量闸口
- "Do not emit passing mentions, known-concept recaps, one-off timestamps, attendance facts, or facts with no reusable value" — 噪声过滤
- "Merge evidence from multiple files when it teaches the same abstraction" — 跨文件合并指导

dream_extract 的 system prompt SHALL 在 Bucket Classification 中包含 `personal` bucket 定义："user/team/project-specific identity, preferences, conventions, constraints, avoidances"。

#### Scenario: Extract prompt contains quality gate language
- **WHEN** `_EXTRACT_SYSTEM_PROMPT` is rendered
- **THEN** the prompt text MUST contain "gate for", "fewer, richer units", "passing mentions", and "merge evidence from multiple files"

#### Scenario: Extract prompt includes personal bucket
- **WHEN** `_EXTRACT_SYSTEM_PROMPT` is rendered
- **THEN** the prompt text MUST contain "personal" bucket classification guidance

### Requirement: Dream Integrate Only-Add Wikilink Principle

dream_integrate 的 system prompt（procedure / personal / wiki 三个 bucket）SHALL 包含以下 only-add 原则：
- "UPDATE must be additive: never remove existing wikilinks or derived_from entries"
- "Default to weaving more, not less"

#### Scenario: Integrate prompts contain only-add principle
- **WHEN** any `_INTEGRATE_SYSTEM_PROMPT_*` is rendered
- **THEN** the prompt text MUST contain "additive" and "weaving more"

### Requirement: Personal Bucket Support

digest 记忆 SHALL 支持三种 bucket：`procedure`、`personal`、`wiki`。

`personal` bucket 的 integrate system prompt SHALL 指导 LLM 输出 "rule of engagement" 格式：
- Rule / fact: 一句话说明偏好、约定、身份事实、约束或 avoid-rule
- Why: 原因或上下文
- How to apply: 适用上下文、任务、边界或例外

`auto_dream._dream_integrate` 的 bucket 路由 SHALL 对 `personal` bucket 使用 `_INTEGRATE_SYSTEM_PROMPT_PERSONAL`。

`auto_dream._dream_extract` 的 LLM 输出中，`bucket` 字段合法值 SHALL 包含 `personal`。当 LLM 输出未知 bucket 值时，SHALL fallback 到 `wiki`。

#### Scenario: Personal bucket create
- **WHEN** dream_extract outputs a unit with `bucket="personal"` and dream_integrate finds no matching existing node
- **THEN** a new digest file SHALL be created under `digest/personal/` with `bucket="personal"` in frontmatter

#### Scenario: Personal bucket integrate prompt
- **WHEN** dream_integrate processes a unit with `bucket="personal"`
- **THEN** `_INTEGRATE_SYSTEM_PROMPT_PERSONAL` SHALL be used as the system prompt

#### Scenario: Unknown bucket fallback
- **WHEN** dream_extract outputs a unit with `bucket="unknown_value"`
- **THEN** the bucket SHALL be normalized to `wiki`

### Requirement: Dream Integrate Two-Round Search

`_dream_integrate` SHALL 执行两轮 node_search：
1. 第一轮：`query=unit.name`，`limit=10`
2. 第二轮：`query=unit.summary`，`limit=10`

两轮结果 SHALL 按 path 去重（保留较高 score），取 top-5 作为 `existing_nodes` 传入 LLM prompt。

当 `node_search` 不可用时，SHALL fallback 到 `HybridSearch.search(query=name, top_k=3, bucket=bucket)`。

#### Scenario: Two-round search deduplication
- **WHEN** both rounds return the same path
- **THEN** the path SHALL appear only once in `existing_nodes`，with the higher score retained

#### Scenario: Node search unavailable fallback
- **WHEN** `self.node_search` is None
- **THEN** HybridSearch SHALL be used with `top_k=3`

### Requirement: Dream Extract Input Protection

`_dream_extract` SHALL 跳过 `.yaml` 文件——interests.yaml 不应作为 extract 输入。

#### Scenario: YAML files excluded from extract
- **WHEN** file_catalog reports a changed `.yaml` file
- **THEN** the file SHALL be skipped and not included in `daily_contents`

### Requirement: Dream Extract Output Path Validation

`_dream_extract` 解析 LLM 输出后，每个 unit 的 `source_paths` SHALL 只包含 `changed_paths` 中存在的路径。不在 `changed_paths` 内的路径 SHALL 被过滤。过滤后 `source_paths` 为空的 unit SHALL fallback 到 `changed_paths[:3]`。

#### Scenario: LLM hallucinated path filtered
- **WHEN** LLM outputs `source_paths=["daily/2026-01-01/fake.md"]` and `"daily/2026-01-01/fake.md"` is not in `changed_paths`
- **THEN** the path SHALL be removed from `source_paths`

#### Scenario: Empty source_paths fallback
- **WHEN** all source_paths are filtered out and changed_paths is non-empty
- **THEN** `source_paths` SHALL be set to `changed_paths[:3]`

### Requirement: Auto Memory File Rename Retarget

`auto_memory._update_card` 中，当 LLM 返回的新 `name` 导致文件路径变化时，SHALL 执行以下 retarget 流程：
1. 写入新文件
2. 删除旧文件
3. 遍历 workspace 中所有 `.md` 文件，调用 `retarget_wikilinks(content, old_rel, new_rel)` 更新引用
4. 更新 wikilink expander 图（移除旧路径边，为新路径重建边）
5. 更新 file catalog（移除旧路径，upsert 新路径）

#### Scenario: Rename retargets wikilinks
- **WHEN** a daily card is renamed from `session_old.md` to `session_new.md` and another file contains `[[session_old.md]]`
- **THEN** the wikilink in the other file SHALL be updated to `[[session_new.md]]`

#### Scenario: Rename no-op when name unchanged
- **WHEN** LLM returns the same `name` as the existing card
- **THEN** no retarget SHALL occur

### Requirement: Topic Title Normalization for Dedup

`_dream_topics` SHALL 使用 `_normalize_topic_title()` 归一化标题进行去重比较：
- 转小写
- 移除标点符号（`[^\w\s]`）
- 压缩多余空格为单个空格

`recent_titles` 和 `existing_titles` SHALL 存储归一化形式。精确字符串匹配 SHALL 被替换为归一化比较。

#### Scenario: Near-duplicate topics deduplicated
- **WHEN** candidate topic title is "Memory Pipeline!" and recent topic title is "memory pipeline"
- **THEN** both normalize to "memory pipeline" and the candidate SHALL be treated as duplicate

### Requirement: Session JSONL Timestamp Normalization

`auto_memory.write_session_jsonl` SHALL 在 `_sanitize_msg_for_save` 之前调用 `_normalize_timestamp()`。

`_normalize_timestamp()` SHALL 将以下别名映射到 `created_at`：
- `time_created`
- `timestamp`
- `createdAt`
- `timeCreated`
- `created_time`

当消息已有 `created_at` 字段时，SHALL 不做任何修改。

#### Scenario: Alias timestamp normalized
- **WHEN** a message dict has `timestamp="2026-08-05T10:00:00Z"` but no `created_at`
- **THEN** `_normalize_timestamp` SHALL add `created_at="2026-08-05T10:00:00Z"` to the dict

#### Scenario: Existing created_at preserved
- **WHEN** a message dict already has `created_at="2026-08-05T10:00:00Z"`
- **THEN** `_normalize_timestamp` SHALL return the dict unchanged
