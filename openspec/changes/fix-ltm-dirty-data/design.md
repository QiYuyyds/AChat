# Design — fix-ltm-dirty-data

## 背景与定位

本变更只解决 LTM 脏数据的两端问题：**进料端灌垃圾** 与 **清理端删不掉**，外加两处会放大不一致的稳定性缺陷。不涉及会话/用户维度隔离（明确排除）。

Preference 与 LTM 的分工是本变更的设计基线：

| | Preference | LTM |
|---|---|---|
| 形态 | 单值、按 key 覆盖 | 多值、追加 |
| 键 | 固定槽位（姓名/喜好/风格） | 无键，向量召回 |
| 注入 | 每轮原样注入 `【用户偏好】` | 按 query 语义相关才召回 |
| 内容 | 稳定档案 | 事实 / 事件 / 策略 / 工具失败 |

分类结果（`classify_memory_content` / `llm_classify_memory` 已返回 7 类 category）是路由依据，当前代码算了却没用——本变更把它接上。

## 决策

### D1. 进料收敛：LTM 只收「分类后的结构化事实」

- 移除 `on_message_end(role="user")` 中的 `_safe_ltm_add(content)` 原文入库。
- LTM 的唯一入口变为 `extract_memory_from_reply` 的抽取结果，且仅当 category ∈ {fact, episodic, policy, tool_failure} 时才 `store_classified`。
- 用户身份/偏好继续走 Preference 抽取链（`extract_and_save` + LLM overlay），不进 LTM。

**取舍**：用户消息里偶有的事实（"我们项目用 PostgreSQL"）现在依赖 assistant 回复复述后被抽取，而非用户原文直接入库。可接受——LTM 的价值在于"值得跨会话召回的事实"，这类事实几乎都会在助手回复里被复述；直接存用户原文的召回价值低、噪声高。

### D2. category 路由：取消双写

`extract_memory_from_reply` 内每条 k-v：

```
category ∈ {identity, preference}        → preference.set(k, v)          （不进 LTM）
category ∈ {fact, episodic, policy,      → ltm.store_classified(...)     （不进 preference）
           tool_failure}
category == general                      → 丢弃
```

移除现有「preference.set 与 store_classified 同时执行」的无条件双写。

### D3. id 以 PG 主键为准

- `load_from_storage`：`item.id = r.id`（不再 `enumerate` 下标），`self._next_id = max([r.id...], default=-1) + 1`。
- `add` / `store_classified` 已用 `row.id` 回填，保持一致。
- 由此 `_sync_consolidation_to_db` 的 `DELETE ... WHERE id IN (...)` 与 `UPDATE ... WHERE id = item.id` 恒命中正确行。
- Graph `mem_id` 随之稳定（同一条记忆 id 跨重启不变）。

### D4. 衰减：增量化 + 持久化（**不改 schema 的默认方案**）

问题：现按"距 `created_at` 总天数"每次固化重复衰减，且 importance 不落库，重启复位。

`long_term_memory` 当前**无** `last_decay_ts` 列。两个方案：

- **方案 A（默认，不改 schema）**：给内存 `Item` 增加运行期字段 `last_decay_ts`；固化时按 `now - item.last_decay_ts` 的增量天数衰减，衰减后把 `importance` 写回 PG（复用已有 `importance` 列），并更新 `item.last_decay_ts = now`。`load_from_storage` 时把 `last_decay_ts` 初始化为 `now`——即重启后不追溯补衰减。**语义**：衰减只在进程运行期累积、且已持久化的 importance 不会再被重复砍。消除"指数塌陷"与"重启复位"两个 bug，代价是跨重启的空档期不计衰减（可接受）。
- **方案 B（需审批）**：新增 `last_decay_ts` 列持久化衰减检查点，跨重启精确续算。涉及 `models.py` schema 变更，按 CLAUDE.md §6.2 需人确认。

**本变更默认走方案 A**；若团队要求跨重启精确衰减，再单开变更走方案 B。

### D5. dedup 落库

dedup 分支（相似度 ≥ dedup_threshold）合并后，除把 `item_j.id` 加入 `delete_from_db` 外，把存活的 `item_i` 加入 `result.update_in_db`（与 merge 分支一致），使 importance/tags/last_accessed 落回 PG。

### D6. 写入并发安全

`add` 与 `store_classified` 的「读改 `self.items` / `self._next_id` / `self._items_since_last`」段落用 `async with self._lock` 包裹。注意：embed 等 `await` I/O 放在锁**外**，只把内存结构变更放锁内，避免长时间持锁。

### D7. 同步 I/O 异步化

`main.py` 里同步 `httpx.Client` 的 embed/generate 闭包，调用点改为 `await asyncio.to_thread(fn, ...)`；涉及 `long_term.py`（embed）、`memory_writer.py`（generate/embed）。签名从"直接调用"改为"可 await"，注意 `store_classified`/`add`/`recall` 已是 async，包裹即可。

## 落地顺序

1. **阶段 1（A：D1+D2）** — 止血，先不再灌脏数据、消除双写。独立可上线。
2. **阶段 2（B1：D3）** — 修 id，让清理能删对行。是阶段 3 的前置。
3. **阶段 3（B2+B3：D4+D5）** — 衰减/去重落库，清理存量。依赖阶段 2。
4. **阶段 4（C：D6+D7）** — 并发与异步收尾。可独立进行。

## 存量清理

id/衰减修复后，存量脏数据需一次性收敛。提供只读体检脚本先量化（总数、重复率、原文类占比、importance 分布），确认后触发一次全量 `consolidate()` 让修复后的删除逻辑生效。首次清理前建议 `pg_dump` 备份 `long_term_memory`。

## 不做

- 不做会话/用户维度隔离（`default_user` 保持）。
- 不动 Preference 的抽取规则与长度上限（`fix-memory-quality` 已处理）。
- 不改 RAG / graph 召回逻辑。
