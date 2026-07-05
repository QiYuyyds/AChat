## Why

Preference 表缺乏语义去重机制，多轮 LLM 提取后会产生大量语义重复的 key-value（如"喜好=Python"/"偏好=Python"/"喜欢=Python"），导致 Profile 槽位膨胀且内容不稳定。同时，memory_writer 已改为单写模式（identity/preference 只写 Preference 表），但 ProfileSource 仍同时从 Preference 和 LTM 两个源获取数据，数据源与写入路径不匹配。Profile 标为 static=True 但内容每轮对话后可能变化，直接破坏 system prompt 前缀稳定性，导致 prompt cache 命中率下降。此外，工具调用错误信息不在 assistant 文本输出中，memory_writer 无法提取 tool_failure 类别记忆。

## What Changes

- **Profile 槽位从 static 改为 dynamic**：4 种 Schema（CHAT/TOOL/REACT/RAG）中 SlotProfile 的 `static` 从 `True` 改为 `False`，Profile 内容通过 `<system-reminder>` 注入 user message 前缀，system prompt 只保留 Constraints（极稳定）
- **ProfileSource 数据源精简**：去掉 `ltm` 参数，只从 Preference 表读取；加入 score 动态计算（复用 `classify_memory_content` + `_IMPORTANCE_BY_CATEGORY`），按 score 降序排列
- **去掉 top_k 限制**：Schema 中 SlotProfile 去掉 `top_k`，只用 `token_budget` 裁剪，避免新增 key 导致截取移位
- **Preference key 归一化**：在 `set` / `save_batch` 中加同义词映射（如"喜欢"→"喜好"、"名字"→"姓名"），写入前归一化
- **LLM 提取传入已有 keys**：`extract_preferences` 接收 `existing_keys` 参数，prompt 中展示已有 key 列表，要求 LLM 复用语义相同的已有 key
- **Preference 定期 LLM 合并**：在 `_safe_consolidate` 中增加 Preference 合并步骤，当条目数超过阈值时用 LLM 检查并合并语义重复的 key-value
- **工具错误信息注入 memory**：`_post_run_memory_hook` 从 parts_list 中提取 `tool_result(isError=True)` 的条目，追加到 agent_text 中传入 memory 子系统，使 `extract_memory_from_reply` 能感知工具调用失败

## Capabilities

### New Capabilities

- `preference-dedup`: Preference 表语义去重机制——key 归一化（同义词映射）、LLM 提取时传入已有 keys、定期 LLM 合并

### Modified Capabilities

- `conversation-context`: Profile 槽位从 static 改为 dynamic、ProfileSource 去掉 LTM 数据源、加入 score 动态计算、去掉 top_k 改用 token_budget + score 排序裁剪

## Impact

- `backend/app/services/prompt_assembler.py` — 4 种 Schema 中 SlotProfile 改 static=False/去掉 top_k；ProfileSource 去掉 LTM 部分、加 score 计算
- `backend/app/main.py` — ProfileSource 注册时去掉 ltm 参数
- `backend/app/memory/preference.py` — 增加 `_normalize_key` 同义词映射，`set`/`save_batch` 写入前归一化
- `backend/app/memory/memory_writer.py` — `extract_preferences` 增加 `existing_keys` 参数，prompt 展示已有 keys
- `backend/app/memory/memory_service.py` — `_safe_llm_extract_preference` 传入已有 keys；`_safe_consolidate` 增加 Preference 合并步骤
- `backend/app/services/agent_runner.py` — `_post_run_memory_hook` 提取工具错误信息（已实施）
