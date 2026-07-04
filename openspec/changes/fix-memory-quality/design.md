## Context

AChat 的记忆系统从 AGI-memory 移植而来，包含三层结构：Preference（规则偏好提取）、LongTerm（embedding 语义记忆）、ShortTerm（滑动窗口）。移植过程中遗漏了部分关键逻辑（LLM 偏好提取、preference.set 双写），同时 AChat 新增的 static/dynamic 渲染和 `_trim_by_budget` 的"至少保留 1 条"逻辑引入了原版不存在的问题。

当前问题集中表现在 ProfileSource 组装阶段：
1. Preference 表存有 2271 字符的对话垃圾（key="喜好"）
2. LTM 存有双重前缀的低质量事实（"用户用户名称: 涵涵"）
3. 所有 importance=0.7 导致排序无意义
4. ProfileSource 不排序 preference keys，导致 static prompt 抖动
5. `_trim_by_budget` 强制保留 1 条让垃圾独占 500 token 预算

约束：
- 不改 DB schema（`LongTermMemory` / `UserPreference` 表结构不变）
- 不改外部 API
- 不改前端
- 改动集中在 `backend/app/memory/` 和 `backend/app/services/prompt_assembler.py`

## Goals / Non-Goals

**Goals:**
- 堵住垃圾数据写入源头（Preference 长度校验 + 前缀去重）
- 稳定 static prompt 内容（key 排序 + trim 简化），恢复 cache 命中
- 让 importance 排序有意义（按 category 分级）
- 对齐原版双写逻辑（extract_memory_from_reply 补写 preference）
- 用 LLM 覆盖规则提取的粗糙偏好值

**Non-Goals:**
- 不做 P3 的 token budget 收紧（保持当前 500/600 不变）
- 不重构记忆架构（保留三层结构 + PromptAssembler 不变）
- 不新增 DB 表或字段
- 不改 consolidation 算法本身
- 不改 GraphMemory 相关逻辑

## Decisions

### Decision 1: Preference.set() 采用截断而非拒绝

**选择**：value 超过 200 字符时截断到 197 字符 + "..."，而非直接拒绝写入。

**理由**：
- 拒绝会丢失规则提取的即时反馈（用户说"我喜欢xxx"时，前端需要"已记住"的回显）
- 截断保留了核心信息（前 197 字符通常够用）
- 后续 LLM 提取会用更精准的值覆盖（Decision 5）

**替代方案**：
- 拒绝写入 + 日志告警 → 简单但影响用户体验
- 句子级截断（取第一个句号前） → 更智能但实现复杂，留作未来优化

### Decision 2: importance 分级表硬编码在 memory_writer.py

**选择**：在 `memory_writer.py` 中定义 `_IMPORTANCE_BY_CATEGORY` 常量表，不引入配置项。

```python
_IMPORTANCE_BY_CATEGORY = {
    "identity": 0.9,
    "preference": 0.7,
    "fact": 0.5,
    "episodic": 0.4,
    "tool_failure": 0.3,
    "policy": 0.8,
    "general": 0.3,
}
```

**理由**：
- 这些值是经验性的，不会频繁调整
- 加配置项（`config.py`）增加了认知负担但收益不大
- 与原版保持一致（原版也是硬编码 0.7）

**替代方案**：
- 走 `Settings` 配置 → 灵活但增加运维复杂度，当前不需要
- 让 LLM 分类时同时输出 importance → 更准但每次多一次 LLM 调用

### Decision 3: `_trim_by_budget` 对齐原版——超预算直接丢弃

**选择**：去掉"至少保留 1 条"逻辑和截断逻辑，回归原版的简洁行为。

```python
def _trim_by_budget(items, budget):
    total = 0
    for i, item in enumerate(items):
        total += len(item.text)
        if total > budget:
            return items[:i]
    return items
```

**理由**：
- 原版的"超了就丢"策略在实践中被验证：宁可空着预算，也不塞垃圾
- AChat 的"至少保留 1 条"是为了"不浪费预算"，但前提是 items 质量高；在质量不可控时反而放大问题
- Decision 1（长度校验）+ Decision 5（LLM 覆盖）从源头保证了 items 质量，让"超了就丢"策略安全

### Decision 4: fact_content 前缀检测用 `str.removeprefix`

**选择**：用 Python 3.9+ 的 `str.removeprefix("用户")` 处理 key，再拼接。

```python
clean_key = str(k).removeprefix("用户")
fact_content = f"用户{clean_key}: {v}"
```

**理由**：
- 一行代码，语义清晰
- 覆盖 "用户名称"、"用户偏好" 等已含前缀的 key
- 对不含前缀的 key 无影响（`removeprefix` 不匹配时返回原串）

### Decision 5: LLM 偏好提取作为异步覆盖层

**选择**：在 `memory_service.py` 的 `on_message_end(role="user")` 中，异步调用 `llm.extract_preferences()` 并用结果覆盖 `preference` 表。

**数据流**：
```
on_message_end("user", content)
  ├─ 同步: preference.extract_and_save()     ← 规则提取，即时回显
  ├─ 异步: _safe_ltm_add()                    ← LTM 写入
  └─ 异步: _safe_llm_extract_preference()     ← 新增：LLM 提取 → save_batch 覆盖
```

**理由**：
- 对齐原版 `async_update_memory` 的双层设计
- 同步规则提供即时反馈（"已记住：喜好=xxx"）
- 异步 LLM 提取更精准的值，用相同 key 覆盖规则提取的结果
- 不阻塞用户交互

**替代方案**：
- 只用 LLM 提取，去掉规则 → 丢失即时反馈，增加延迟
- 只用规则，不加 LLM → 保持现状，垃圾问题不解决

### Decision 6: ProfileSource 排序只对 Preference，不对 LTM

**选择**：只对 `pref.snapshot()` 的 keys 做 `sorted()`，LTM items 保持按 importance 排序。

**理由**：
- Preference 是确定性事实（key-value 对），排序后稳定
- LTM items 按 importance 排序是有意义的（高优先级在前），且 importance 分级后排序更稳定
- LTM items 的排序由 `filter_by_category` 的返回顺序决定（已按 importance desc），不需要额外排序

## Risks / Trade-offs

**[Risk] LLM 偏好提取增加 API 调用** → 只在 `_generate_fn` 可用且消息非短时触发（复用已有的 `_is_trivial_reply` 判断）；失败时静默降级到规则结果。

**[Risk] 截断 200 字符可能丢失合法的长偏好** → 真正的偏好（喜好、姓名、字体、风格）通常 <100 字符；超 200 字符的 99% 是对话片段。如果确实有合法长偏好，LLM 提取层会用精准值覆盖。

**[Risk] `_trim_by_budget` 去掉"至少保留 1 条"后 profile slot 可能为空** → 如果所有 preference + LTM items 都超预算，确实会空。但这意味着数据本身有问题（Decision 1 的长度校验已防止），空比塞垃圾好。

**[Trade-off] importance 硬编码 vs 可配置** → 选择硬编码，牺牲灵活性换取简单性。未来如果需要调优，可以提取到 `Settings`。
