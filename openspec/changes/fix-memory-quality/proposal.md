## Why

记忆系统存在多个数据质量问题，导致 prompt 注入垃圾数据并破坏 prompt cache 命中率。根因可追溯到 AGI-memory 原版移植时的遗漏（`llm.extract_preferences` 未移植、`preference.set()` 漏写）和 AChat 新增机制引入的副作用（static/dynamic 渲染把不稳定数据锁入缓存、`_trim_by_budget` 的"至少保留 1 条"让垃圾独占预算）。这些问题叠加在一起，使 profile slot 的 500 token 预算被一条 2271 字符的对话片段占满，真正有价值的身份和偏好信息（姓名、字体、设计风格）全部被挤出。

## What Changes

- **Preference 写入长度校验**：`preference.set()` 拒绝或截断超过 200 字符的 value，从源头堵住对话垃圾
- **ProfileSource key 排序**：`ProfileSource.fetch()` 对 preference keys 做 `sorted()` 后再渲染，确保 static prompt 字符序列跨 run 稳定
- **`_trim_by_budget` 简化**：去掉"至少保留 1 条"逻辑，对齐原版行为——超预算即丢弃，不做截断保留
- **`extract_memory_from_reply` 补写偏好**：补回 `preference.set(k, v)` 调用，对齐原版双写（偏好仓 + LTM）
- **importance 按 category 分级**：identity=0.9, preference=0.7, fact=0.5, episodic=0.4, 其他=0.3，替换硬编码 0.7
- **补回 LLM 偏好提取**：新增 `extract_preferences()` 方法（LLM 提取 → 规则 fallback），在 `on_message_end(role="user")` 异步调用覆盖规则提取的粗糙结果
- **`fact_content` 去双重前缀**：拼接前检测 key 是否已含"用户"前缀，避免"用户用户名称"

## Capabilities

### New Capabilities

- `memory-quality`: 记忆系统数据质量规则——写入校验、importance 分级、前缀去重、LLM 偏好提取覆盖

### Modified Capabilities

- `conversation-context`: ProfileSource 渲染顺序稳定化 + `_trim_by_budget` 裁剪策略修正，影响 prompt cache 稳定性

## Impact

- **后端代码**：`backend/app/memory/preference.py`、`backend/app/memory/memory_writer.py`、`backend/app/memory/memory_service.py`、`backend/app/services/prompt_assembler.py`
- **数据库**：无 schema 变更；需要一次性清理 Preference 表中超长垃圾数据（可作为 consolidation 增强自动处理）
- **API**：无外部 API 变更
- **前端**：无影响
- **风险**：改动集中在记忆写入和 prompt 装配路径，是 agent 每次对话的热路径；需要充分测试不破坏正常记忆流
