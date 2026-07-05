## Context

memory_writer 已改为单写模式：identity/preference 类别的条目只写入 Preference 表，不再写入 LTM。但 ProfileSource 仍同时从 Preference 和 LTM 两个源获取 identity/preference 数据，导致数据源与写入路径不匹配。Preference 表缺乏语义去重机制（LTM 有 cosine similarity 去重，Preference 只有同 key 去重）。Profile 标为 static=True 但内容每轮对话后可能变化（_safe_llm_extract_preference + extract_memory_from_reply），直接破坏 system prompt 前缀稳定性。

## Goals / Non-Goals

**Goals:**
- system prompt 只包含 Constraints（极稳定），缓存命中率从 ~0% 提升到 70-80%
- Preference 表无语义重复 key-value（三层去重防线）
- ProfileSource 数据源与 memory_writer 写入路径一致（只从 Preference 表读取）
- score 按 category 语义动态计算，token_budget 裁剪时保留高分条目
- 工具调用错误信息可被 memory_writer 提取为 tool_failure 类别

**Non-Goals:**
- 给 Preference 表加 score 列（score 在 ProfileSource 中动态计算，不持久化）
- 给 Preference 表加 embedding 列（不做 cosine 去重，用 key 归一化 + LLM 合并替代）
- 跨用户 Preference 隔离（当前固定 default_user）
- 修改 LTM 的 store_classified 或 consolidation 逻辑

## Decisions

### D1: Profile 从 static 改为 dynamic，而非做快照

**选择**：将 4 种 Schema 中 SlotProfile 的 static 从 True 改为 False。

**理由**：做快照意味着会话内 Profile 内容不变，用户本轮说的新偏好要下一轮才生效。而移到 dynamic 后，每轮都从 Preference 表实时读取，新偏好立即生效。同时 system prompt 只保留 Constraints（几乎不变），缓存命中率更高。Dynamic 内容通过 `<system-reminder>` 注入 user message 前缀，不破坏 system prompt 缓存。

**替代方案**：会话内快照 Preference，下一轮刷新。但用户在对话中说了大量偏好时本轮不生效，体验差。

### D2: score 复用 classify_memory_content 动态计算，不加表结构

**选择**：在 ProfileSource.fetch() 中调用 classify_memory_content(key, value) 获取 category，再用 _IMPORTANCE_BY_CATEGORY 映射 score。

**理由**：Preference 表只有 user_id/key/value/updated_at 四列，加 score 列需要 migration 且 memory_writer 写入时需赋值。而 classify_memory_content 已有完善的规则（姓名→identity, 喜好→preference），_IMPORTANCE_BY_CATEGORY 已定义各类别分数（identity=0.9, preference=0.7），直接复用零成本。

**替代方案**：给 Preference 表加 score 列，memory_writer 写入时赋值。但需要 migration 且 score 不会动态变化（一旦写入就是固定的），不如运行时计算灵活。

### D3: 去掉 top_k，只用 token_budget + score 排序裁剪

**选择**：Schema 中 SlotProfile 去掉 top_k 参数，ProfileSource 按 score 降序排列后只用 token_budget 裁剪。

**理由**：top_k 按 sorted(keys) 顺序截取前 N 个，新增 key 会导致截取结果移位（即使内容大部分相同）。去掉 top_k 后，只要总 token 不超预算就全保留；超预算时按 score 裁剪最低分的，新增 key 不会影响高分条目。

**替代方案**：保留 top_k 但按 score 排序后截取。但 top_k=6-10 太小，且 score 相同时排序不稳定。

### D4: 三层去重防线，而非单一机制

**选择**：
1. 写入时 key 归一化（同义词映射，如"喜欢"→"喜好"）
2. LLM 提取时传入已有 keys（prompt 展示已有 key 列表）
3. 定期 LLM 合并（条目数超阈值时用 LLM 检查并合并）

**理由**：LTM 有 embedding cosine 去重，但 Preference 表没有 embedding 也不应加。第 1 层覆盖最常见的同义词（成本零），第 2 层让 LLM 在提取时就避免造近义新 key（成本低），第 3 层兜底处理前两层遗漏的情况（成本较高但频率低）。

**替代方案**：给 Preference 加 embedding 做 cosine 去重。但 Preference 是简单 key-value 对，embedding 对短文本效果差，且增加 Milvus 依赖和计算成本。

### D5: 工具错误信息从 parts_list 提取，而非改 CustomAdapter

**选择**：在 _post_run_memory_hook 中从 Message parts_list 提取 tool_result(isError=True) 的条目，追加到 agent_text 传入 memory 子系统。

**理由**：CustomAdapter 已经将 tool_result 存入 parts_list（persist_event L1080），_post_run_memory_hook 已经在遍历 parts_list 提取 text。只需在同一遍历中额外提取 tool_result(isError=True)，无需改 CustomAdapter 的流式逻辑。

**替代方案**：在 CustomAdapter 中工具失败时主动调用 memory_service。但 CustomAdapter 不应依赖 memory_service，职责耦合。

## Risks / Trade-offs

- **[Profile 移到 dynamic 后 LLM 可能不再重视用户画像]** → `<system-reminder>` 标签是 LLM 能识别的语义标签，且 Constraints 中可补充"注意 system-reminder 中的用户画像"引导
- **[key 归一化同义词表不完整]** → 第 2 层 LLM 传入已有 keys 和第 3 层 LLM 合并作为兜底；同义词表可逐步扩充
- **[LLM 合并可能误合并语义不同的 key]** → 合并 prompt 要求 LLM 只合并"语义明确相同"的条目，且合并后保留更具体的 value
- **[classify_memory_content 规则可能漏分类]** → 未匹配的 key 默认 score=0.3（general），不会影响高分类目条目的保留
