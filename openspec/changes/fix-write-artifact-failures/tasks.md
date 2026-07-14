## 1. Phase 1 — max_tokens 设置 + 截断检测

- [x] 1.1 在 `backend/app/utils/model_registry.py` 的 `ModelLimits` 中新增 `max_output_tokens: int | None` 字段，为已知模型补充输出硬上限（如 gpt-3.5-turbo=4096, deepseek-chat=8192）
- [x] 1.2 在 `backend/app/adapters/custom_adapter.py` 的 `call_once` 方法中，调用 `get_model_limits` 推导 `max_tokens` 并传入 `client.chat.completions.create`
- [x] 1.3 在 `backend/app/adapters/custom_adapter.py` 的 `stream` 方法中做同样的 `max_tokens` 设置
- [x] 1.4 在 `call_once` 的 tool_calls 解析段，当 `json.loads(args_buffer)` 失败且 `finish_reason == "length"` 时，不传空 `{}`，而是直接 emit `ToolResultEvent` 带截断错误消息
- [x] 1.5 在 `stream` 方法的 tool_calls 解析段做同样的截断检测
- [x] 1.6 新增测试用例：模拟 `finish_reason="length"` + args 截断场景，验证返回截断错误而非空参数校验失败

## 2. Phase 1 — 错误消息增强

- [x] 2.1 在 `backend/app/tools/write_artifact.py` 的 `_handler` 中，将 `ValidationError` 的返回消息改为结构化格式：列出缺失字段名 + 期望格式
- [x] 2.2 在 `_handler` 的 `build_artifact_content` 失败分支中，返回包含期望格式示例 + 收到内容预览的错误消息
- [x] 2.3 为每种 artifact type 维护一个期望格式示例字符串（web_app/document/image/diagram/ppt）
- [x] 2.4 新增测试用例：验证各种校验失败场景的错误消息包含期望格式和预览

## 3. Phase 2 — Mermaid 校验增强

- [x] 3.1 在 `backend/app/utils/mermaid_normalize.py` 中新增 `_infer_diagram_type` 函数，根据源码内容推断图类型
- [x] 3.2 在 `normalise_mermaid_source` 中，当 declaration 缺失时调用 `_infer_diagram_type` 自动补全
- [x] 3.3 修改 `_FENCE_RE` 正则，支持围栏前后有空白字符（`\s*$` 结尾）
- [x] 3.4 确认 `_NODE_LABEL_RE` 和 `_escape_mermaid_label` 正确处理 Unicode 字符（中文/日文/韩文）
- [x] 3.5 新增测试用例：缺失 declaration、多行围栏、中文 label 场景

## 4. Phase 2 — Content 格式容错增强

- [x] 4.1 在 `backend/app/services/artifact_service.py` 的 `_build_web_app` 中新增 `src`、`body` key 别名
- [x] 4.2 在 `_build_document` 中新增 `body` key 别名
- [x] 4.3 在 `_build_image` 中新增 `src`、`link` key 别名
- [x] 4.4 在 `_build_ppt` 中支持 `slides` 为单个 dict 时自动包成数组；新增 `pages` key 别名
- [x] 4.5 在 `_build_diagram` 中新增 `graph` key 别名
- [x] 4.6 新增测试用例：各类型的新 key 别名场景

## 5. Phase 2 — 工具描述精简

- [x] 5.1 在 `backend/app/tools/write_artifact.py` 中将 `_CONTENT_DESCRIPTION` 精简为 per-type one-liner 格式示例 + JSON 反序列化提醒（不超过 10 行）
- [x] 5.2 验证精简后的描述仍包含所有 5 种 type 的格式示例和 "不要 JSON 字符串化" 警告

## 6. Phase 3 — update_artifact 工具

- [x] 6.1 创建 `backend/app/tools/update_artifact.py`，实现 `update_artifact_tool`（参数：artifactId, addFiles, updateFiles, removeFiles）
- [x] 6.2 实现 web_app 类型检查、文件数限制（20）、单文件大小限制（100KB）、路径安全检查
- [x] 6.3 直接修改 artifact 的 `content_dict`（不创建新版本行），返回更新的文件列表
- [x] 6.4 在 `backend/app/tools/registry.py` 中注册 `update_artifact_tool`
- [x] 6.5 在 `backend/app/services/agent_runner.py` 的 `_build_agent_hub_tool_guidance` 中增加 `update_artifact` 使用说明
- [x] 6.6 新增测试用例：追加文件、更新文件、删除文件、非 web_app 拒绝、artifact 不存在、文件数超限

## 7. Spec 文档同步

- [x] 7.1 更新 `specs/07-tools.md`：新增 `update_artifact` 工具签名和说明
- [x] 7.2 更新 `specs/04-artifacts.md`：记录 content key 别名扩展和 Mermaid 校验增强
- [x] 7.3 更新 `specs/05-adapter-interface.md`：记录 CustomAdapter max_tokens 设置和截断检测

## 8. 集成验证

- [x] 8.1 运行 `ruff check .` 确保无 lint 错误（新代码部分）
- [x] 8.2 运行 `pytest backend/tests/test_tools.py` 确保工具测试通过
- [x] 8.3 运行 `pytest backend/tests/test_artifact_service.py` 确保 artifact 服务测试通过
- [x] 8.4 运行 `pytest backend/tests/test_custom_adapter.py` 确保 adapter 测试通过
- [ ] 8.5 手动验证：使用 Custom Agent 创建各类型产物（web_app/document/diagram/ppt），确认无报红

## 9. 第二轮测试修复 — 工具可用性与错误消息

- [x] 9.1 在 `backend/app/services/agent_runner.py` 中为 SDK (Custom) agent 自动注入 companion 工具（`read_artifact`、`update_artifact`、`deploy_artifact`），当 `write_artifact` 在 tool_names 中时
- [x] 9.2 在 `_build_agent_hub_tool_guidance` 的 `write_artifact` 指引中增加截断恢复策略提示，`update_artifact` 指引中增加截断恢复说明
- [x] 9.3 修复 `write_artifact.py` 的 `_format_error`：当 type 缺失或未知时，列出所有可用 type 及其格式示例，而非显示 "(no example available)"
- [x] 9.4 修复 `write_artifact.py` 的 `ValidationError` 处理：当 type 缺失时，在 detail 中列出所有合法 type 枚举值
- [x] 9.5 新增测试用例 `test_write_artifact_empty_args_shows_all_types`：验证空参数调用时错误消息包含所有 type 示例

## 10. 第三轮修复 — 空参数恢复增强

- [x] 10.1 创建 `_try_extract_json` 辅助函数：支持从纯 JSON、markdown 代码块（```json ... ```）、嵌入文本中的 JSON 对象三种策略提取
- [x] 10.2 创建 `_recover_tool_args` 辅助函数：从 `text_buffer` 和 `reasoning_buffer` 中尝试恢复工具参数
- [x] 10.3 在 `call_once` 中用 `_recover_tool_args` 替换原有的简单 `text_buffer.strip().startswith("{")` 检查
- [x] 10.4 在 `stream` 方法中添加缺失的参数恢复逻辑（之前完全没有）
- [x] 10.5 当参数恢复失败且 `finish_reason == "length"` 时，emit 截断错误（之前空参数不检查截断）
- [x] 10.6 当参数恢复失败且非截断时，emit `_EMPTY_ARGS_ERROR_MSG`（明确告知 LLM 参数未收到）
- [x] 10.7 新增单元测试：`_try_extract_json` 的 6 种场景（直接 JSON、markdown 块、嵌入文本、无 JSON、嵌套对象）
- [x] 10.8 新增单元测试：`_recover_tool_args` 的 3 种场景（text_buffer 恢复、reasoning_buffer 恢复、无法恢复）
- [x] 10.9 新增集成测试：从 text_buffer markdown 块恢复参数、从 reasoning_content 恢复参数、无法恢复时 emit 错误
