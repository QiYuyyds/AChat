## Why

长期记忆（LTM）里积累了大量脏数据，根因是**进料端什么都灌、清理端又删不掉**：

- **进料端**：`on_message_end(role="user")` 无条件把用户原文 `_safe_ltm_add` 存进 LTM（`memory_service.py:145`），零过滤零去重——"帮我写个函数""继续""好的"这类对话流水全部入库；同时 `extract_memory_from_reply` 把抽取出的同一份 k-v **同时**写进 Preference 和 LTM（双写），且算出的 `category` 完全没用于路由，导致同一事实在两个库以不同措辞重复存在。
- **清理端**：固化（consolidation）本应兜底，但三处 bug 使其失效——`load_from_storage` 把 LTM id 重写成列表下标丢掉 PG 主键（`long_term.py:84-86`），使 `_sync_consolidation_to_db` 按 `item.id` 删除时**删错行/删不掉**；衰减按"距创建总天数"重复施加且**不写回 PG**（`long_term.py:466-468`），重启即复位，记忆永不过期；dedup 分支合并后漏写 `update_in_db`（`long_term.py:484-491`），去重结果不落库。
- **稳定性**：写入路径 `add`/`store_classified` 全程无锁，与持锁的 `recall`/`consolidate` 竞态；embed/generate 是同步 `httpx.Client` 直接在协程里调用，阻塞事件循环。

净效果：记忆进得猛、出不去、还删不对，脏数据只增不减，并挤占 prompt 注入预算。

本变更承接已落地的 `fix-memory-subsystem`（补齐抽取/持久化）与 `fix-memory-quality`（偏好质量），进一步**明确 Preference 与 LTM 的分工、堵住脏数据源头、修复固化一致性**。其中「取消 Preference↔LTM 双写」是对 `fix-memory-quality` 中"补回双写"的有意演进——双写在当时是对齐 AGI-memory 原版，现按 AChat 实际需要改为按 category 单一归属。

## What Changes

**A. 进料端——少产生脏数据**

- **A1 砍掉原文入库**：`on_message_end(role="user")` 不再 `_safe_ltm_add` 用户原文。LTM 仅由分类后的结构化抽取结果喂入。
- **A2 按 category 分流、取消双写**：`extract_memory_from_reply` 用已算出的 `category` 路由——`identity`/`preference` → 只进 Preference；`fact`/`episodic`/`policy`/`tool_failure` → 只进 LTM；`general` → 丢弃。移除"同一 k-v 双写两库"。

**B. 清理端——让固化真正生效**

- **B1 统一 id 体系**：`load_from_storage` 保留 PG 主键 `r.id` 作为 `item.id`，`_next_id = max(existing ids)+1`，不再用列表下标。使固化的 DELETE/UPDATE 命中正确行。
- **B2 衰减增量化 + 持久化**：衰减按 `now - last_decay_ts` 的增量天数计算（而非距创建总天数），并把衰减后的 importance 与 `last_decay_ts` 落回 PG。
- **B3 dedup 落库**：dedup 分支合并后把 `item_i` 加入 `result.update_in_db`，使去重结果持久化。

**C. 稳定性**

- **C1 写入加锁**：`add`/`store_classified` 的内存写入段纳入 `self._lock`，消除与 `recall`/`consolidate` 的竞态与重复 id。
- **C2 同步 I/O 异步化**：embed/generate 调用用 `asyncio.to_thread(...)` 包裹（或改 `AsyncClient`），不再阻塞事件循环。

## Capabilities

### New Capabilities

- `memory-consolidation`: 记忆固化的正确性契约——增量衰减 + 持久化、dedup/merge 结果落库、按 PG 主键精确删除。

### Modified Capabilities

- `memory-extraction`: 抽取结果按 category 单一归属路由；取消 Preference↔LTM 双写；停止用户原文无差别入库。
- `memory-persistence`: LTM id 以 PG 主键为准；写入路径并发安全；embed/generate 异步化不阻塞事件循环。

## Impact

- **后端代码**：`backend/app/memory/memory_service.py`、`backend/app/memory/memory_writer.py`、`backend/app/memory/long_term.py`、`backend/app/main.py`（embed/generate 异步化）
- **数据库**：无 schema 变更（`long_term_memory` 已有 `last_accessed`；`last_decay_ts` 若无列则复用 `last_accessed` 语义或新增，见 design）；需一次性清理存量脏数据（见 tasks 第 6 节）
- **API**：无外部接口变更
- **前端**：无影响
- **风险**：改动集中在记忆写入与固化路径（agent 每轮对话热路径）；A2 改变 Preference/LTM 归属边界，需回归验证正常记忆流不被破坏；B1 id 修复后首次固化会真正删除存量脏数据，需先备份或灰度
