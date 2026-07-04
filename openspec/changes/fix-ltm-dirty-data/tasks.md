## 阶段 1 — 进料收敛（A：D1 + D2）

- [x] 1.1 `memory_service.py` `on_message_end(role="user")`：移除 `_safe_ltm_add(content, importance)` 原文入库；保留 Preference 抽取链（`_safe_extract_preference` + `_safe_llm_extract_preference`）
- [x] 1.2 `memory_writer.py` `extract_memory_from_reply`：按 `category` 路由——`identity`/`preference` 只 `preference.set`；`fact`/`episodic`/`policy`/`tool_failure` 只 `ltm.store_classified`；`general` 丢弃。移除无条件双写
- [x] 1.3 `memory_service.py`：若 `_safe_ltm_add`、`_estimate_importance` 不再被引用则删除（避免留废代码，遵守 §4.3）
- [x] 1.4 验证：对真实 PG 跑 `extract_memory_from_reply` 集成验证——identity→只进 Preference（LTM 无行）、fact→只进 LTM（category=fact，preference 无写）、general→两库均不写。PASS
- [x] 1.5 验证：用户消息路径已无任何 LTM 写入（`_safe_ltm_add` 已删），纯对话流水结构上不可能进 LTM。体检脚本亦确认存量 27 行原文流水（16.3%）即此前该路径的产物

## 阶段 2 — id 体系统一（B1：D3）

- [x] 2.1 `long_term.py` `load_from_storage`：`item.id = r.id`（移除 `enumerate` 下标重写）；`self._next_id = max((r.id for r in rows), default=-1) + 1`
- [x] 2.2 验证：对真实 PG（166 行存量）`load_from_storage` 后，测试项 `item.id`==PG 主键（loaded 472==pg 472），`_next_id`==max(id)+1，170 项 id 全局唯一。dedup 测试进一步确认 `_sync_consolidation_to_db` 的 DELETE 按主键命中真实行。PASS
- [x] 2.3 验证：`mem_id` 由 `item.id` 派生（`graph_memory.py:_mem_id`），而 `item.id` 现恒等于 PG 主键（2.1）→ 跨重启稳定；已消除旧 `hash(content)` 兜底的不稳定路径

## 阶段 3 — 固化一致性（B2 + B3：D4 + D5）

- [x] 3.1 `consolidation.py` `Item`：增加运行期字段 `last_decay_ts`（默认 `= created_at`）
- [x] 3.2 `long_term.py` `load_from_storage`：加载项 `last_decay_ts = now`（不追溯补衰减，见 design D4 方案 A）
- [x] 3.3 `long_term.py` `consolidate` Phase 1：衰减改为 `days = (now - item.last_decay_ts)/86400`；衰减后 `item.last_decay_ts = now`，并把该 item 加入 `result.update_in_db` 使 importance 落库
- [x] 3.4 `long_term.py` `consolidate` dedup 分支（`sim >= dedup_threshold`）：合并后把存活的 `item_i` 加入 `result.update_in_db`
- [x] 3.5 验证：对真实 PG，10 天龄记忆首次 `consolidate()` 0.7→0.66578，二次 `consolidate()` 仍 0.66578（不重复衰减），且经 `_sync_consolidation_to_db` 后 PG importance==0.66578。PASS
- [x] 3.6 验证：对真实 PG 写入两条相同 embedding 记忆并 `consolidate()`——合并成 1 条，被删行从 PG 移除，存活行 importance=max(0.6,0.9)=0.9、tags 合并为 {ta,tb} 均已落库。PASS

## 阶段 4 — 并发与异步（C：D6 + D7）

- [x] 4.1 `long_term.py` `add` / `store_classified`：把 `self.items` / `self._next_id` / `self._items_since_last` 的读改段用 `async with self._lock` 包裹（embed 等 I/O 留在锁外）
- [x] 4.2 `main.py`：embed/generate 闭包保持同步实现，调用点改异步
- [x] 4.3 `long_term.py`（embed）、`memory_writer.py`（generate/embed）：所有同步 embed/generate 调用改为 `await asyncio.to_thread(fn, ...)`
- [x] 4.4 验证：对真实 PG `asyncio.gather` 并发 12 次 `add`——12 项内存 id 唯一、12 行 PG 主键唯一。PASS
- [~] 4.5 验证：`embed`/`generate` 已全部 `asyncio.to_thread` 包裹（4.3），且 `add` 的 embed I/O 在锁外——事件循环不再被同步 HTTP 阻塞（代码层已保证）。人为拖慢 embed 的压测未单独执行

## 阶段 5 — 回归

- [~] 5.1 `cd backend && ruff check .`：本变更编辑的文件（`long_term.py`/`memory_writer.py`/`memory_service.py`/`consolidation.py`/`test_memory_writer.py`）经 `--select F,B,SIM` 检查**无新增真错误**（仅剩 3 处既有 F401/B905/SIM105，均在未改动行）。全仓 `ruff check .` 有 460 处既有风格债（UP/I），非本变更引入、超出范围，未处理
- [x] 5.2 `cd backend && pytest` 通过（memory 相关用例全绿：`test_memory_writer` / `test_memory_long_term` / `test_sync_consolidation` / `test_long_term_filter` 84 passed；`test_memory_writer.py` 已按新 category 路由契约更新。注：`test_api_agents`/`test_claude_adapter`/`test_conversation_service`/`test_rag_hybrid` 的 10 处失败为既有失败，已 stash 对照确认与本变更无关）
- [x] 5.3 端到端（集成级）：对真实 PG 跑抽取→路由→持久化全链路，确认 identity/fact/general 分流正确、无 Preference↔LTM 双写、无对话流水入库（见 1.4）。所有 ZZTEST_ 测试行已清理，存量 166 行未受影响

## 阶段 6 — 存量清理（一次性）

- [x] 6.1 只读体检脚本 `backend/scripts/ltm_health_check.py` 已交付并运行。存量 166 行报告：importance 84% 挤在 0.7、最低 0.403（几乎不衰减/从不过期）；category general+none=53% 噪声；27 行（16.3%）为对话原文流水；6% 完全重复
- [x] 6.2 备份：`backend/backups/ltm_backup_pre_cleanup.json`（166 行全字段 JSON 快照，可回滚）
- [x] 6.3 全量 `consolidate()` + 针对性清原文流水：consolidate 去重/合并删 60 行（deduped=13 merged=47），再清 17 行 category 空的对话原文垃圾（问候/提问/`<context>` XML 块），共 166→89
- [x] 6.4 复跑体检对比：完全重复 6%→0%；对话原文流水 27→1（保留 1 条合法 episodic）；category 空 27→0；无 embedding 1.8%→0%
