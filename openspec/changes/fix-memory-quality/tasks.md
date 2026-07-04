## 1. Preference 写入校验（P0）

- [x] 1.1 在 `backend/app/memory/preference.py` 的 `set()` 方法中添加长度校验：value 超过 200 字符时截断为前 197 字符 + "..."
- [x] 1.2 为 `preference.set()` 长度校验编写单元测试：覆盖正常值、超长值、边界值（200/201 字符）

## 2. ProfileSource key 排序稳定化（P0）

- [x] 2.1 在 `backend/app/services/prompt_assembler.py` 的 `ProfileSource.fetch()` 中，将 `for k, v in prefs.items()` 改为 `for k in sorted(prefs.keys())` 后遍历
- [x] 2.2 为 ProfileSource 排序编写单元测试：验证不同插入顺序的相同 preference 数据产出相同 ContextItem 序列

## 3. `_trim_by_budget` 简化（P1）

- [x] 3.1 重写 `backend/app/services/prompt_assembler.py` 的 `_trim_by_budget()` 函数：去掉"至少保留 1 条"和截断逻辑，回归原版行为——超预算即丢弃当前及后续 items
- [x] 3.2 更新 `_trim_by_budget` 相关测试：覆盖全部在预算内、部分超出、第一条即超出三种场景

## 4. extract_memory_from_reply 补写偏好（P1）

- [x] 4.1 在 `backend/app/memory/memory_writer.py` 的 `extract_memory_from_reply()` 中，遍历 k-v pairs 时增加 `preference.set(k, v)` 调用（包裹 try/except）
- [x] 4.2 修改 `extract_memory_from_reply` 函数签名，增加 `preference` 参数（类型用 Optional Protocol，暴露 `set(key, value)` 方法）
- [x] 4.3 更新 `memory_service.py` 的 `_safe_extract_memory()` 调用处，把 `self.preference` 传入 `extract_memory_from_reply`
- [x] 4.4 为双写逻辑编写单元测试：验证 LLM 提取的 k-v 同时写入 preference 和 LTM

## 5. importance 按 category 分级（P1）

- [x] 5.1 在 `backend/app/memory/memory_writer.py` 中定义 `_IMPORTANCE_BY_CATEGORY` 常量表：identity=0.9, policy=0.8, preference=0.7, fact=0.5, episodic=0.4, tool_failure=0.3, general=0.3
- [x] 5.2 将 `extract_memory_from_reply()` 中硬编码的 `importance = 0.7` 替换为 `_IMPORTANCE_BY_CATEGORY.get(category, 0.3)`
- [x] 5.3 为 importance 分级编写单元测试：验证不同 category 得到对应 importance 值

## 6. fact_content 去双重前缀（P2）

- [x] 6.1 在 `backend/app/memory/memory_writer.py` 的 `extract_memory_from_reply()` 中，拼接 fact_content 前用 `str(k).removeprefix("用户")` 清理 key
- [x] 6.2 为前缀去重编写单元测试：覆盖已含"用户"前缀和不含前缀两种 key

## 7. LLM 偏好提取覆盖层（P2）

- [x] 7.1 在 `backend/app/memory/memory_writer.py` 中新增 `extract_preferences(generate_fn, msg)` 异步函数：用 LLM 从用户消息提取偏好 JSON，失败时 fallback 到 `_extract_rule_based(msg)`
- [x] 7.2 在 `backend/app/memory/memory_writer.py` 中新增 `_extract_rule_based(msg)` 函数：移植原版规则（"我喜欢"/"我爱"/"我叫"），返回 `Dict[str, str]`
- [x] 7.3 在 `backend/app/memory/memory_service.py` 的 `on_message_end(role="user")` 中，新增异步调用 `_safe_llm_extract_preference(content)`：调用 `extract_preferences` 并将结果 `save_batch` 到 preference
- [x] 7.4 为 `extract_preferences` 编写单元测试：覆盖 LLM 成功、LLM 返回非法 JSON、generate_fn 为 None 三种场景

## 8. 数据清理与集成验证

- [x] 8.1 编写一次性数据清理逻辑（可作为 management command 或 consolidation 增强）：删除 Preference 表中 value 长度 >200 字符的条目
- [x] 8.2 端到端验证：模拟一轮完整对话，确认 Preference 表无垃圾、LTM importance 分级正确、static prompt 跨 run 字节一致
