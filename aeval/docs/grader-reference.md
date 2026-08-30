# Grader 参考

每个任务（`EvalTask.graders`）配置一个或多个评分器。通用字段：

| 字段 | 默认 | 说明 |
|------|------|------|
| `type` | 必填 | `code` / `model` / `state` / `tool_calls` / `transcript` / `artifact` / `metric` / `custom` |
| `name` | 必填 | 评分器名（正则 `^[a-zA-Z][a-zA-Z0-9_-]*$`），路由到具体实现 |
| `weight` | 1.0 | 加权评分权重（`WEIGHTED` / `HYBRID` 策略） |
| `required` | false | `HYBRID` 策略下必须通过，否则任务失败 |
| `sample_count` | 1 | LLM Judge 多采样次数（1-10，算 confidence） |
| `dependencies` | [] | 依赖的其他评分器名（拓扑排序；依赖未通过 → 本评分器跳过） |
| `config` | {} | 类型特定配置（见下） |

## code_based（type: code）— 确定性检查

```yaml
- type: code
  name: code_based
  config:
    threshold: 1.0        # 必须通过的比例
    checks:
      - { type: contains,      target: transcript, value: "80" }
      - { type: not_contains,  target: outcome,    value: "password" }
      - { type: regex,         target: transcript, value: "\\d{2,5}" }
      - { type: exact,         target: outcome,    value: "ok" }
```

- `target`: `transcript`（对话记录全文）| `outcome`（环境最终状态 JSON）| `spans`
- `threshold` < 1.0 时按通过比例给部分分

## model_based（type: model）— LLM-as-Judge

```yaml
- type: model
  name: model_based
  sample_count: 3          # 多采样 → confidence
  config:
    rubric: "回答必须准确且引用了文件内容"
    dimensions: ["correctness", "completeness"]
    threshold: 0.7
```

需要 LLM 回调（`EvalRunner(llm_fn=...)` 或指标注册表注入）；未配置时返回带原因的 0 分结果。

## state_check（type: state）— 环境状态检查

```yaml
- type: state
  name: state_check
  config:
    expectations:
      - { type: file_exists,   path: "output.py" }
      - { type: file_contains, path: "output.py", value: "def main" }
      - { type: db_record,     table: "users", match: {"id": 1} }
```

## tool_calls（type: tool_calls）— 工具调用验证

```yaml
- type: tool_calls
  name: tool_calls
  config:
    required_tools: ["fs_read", "fs_write"]   # 必须调用过
    forbidden_tools: ["bash"]                  # 禁止调用
```

数据来自 trace spans（需要真实 TraceProvider）。

## transcript（type: transcript）— 转录分析

```yaml
- type: transcript
  name: transcript
  config:
    max_turns: 20        # 超出扣分
    max_tokens: 10000
```

按轮次数 / token 冗余度评分（token 数从 spans 的 `agenthub.total_tokens` 提取）。

## artifact_check（type: artifact）— 产物检查

```yaml
- type: artifact
  name: artifact_check
  config:
    expected_type: "code_file"     # 期望产物类型（outcome.artifacts[].type）
    content_regex: "^#.*"          # 可选：产物内容正则
    threshold: 1.0
```

## human（type: custom, name: human）— 人工评分

```yaml
- type: custom
  name: human
  config:
    instructions: "按正确性与清晰度打分"
```

**pending 语义**：评分请求落库（`human_score_requests` 表），该 trial 记为 pending（不计入通过率）；经 `POST /runs/{run_id}/human-scores` 回传分数后汇总重算。配合 Dashboard 或 API 做人工复核闭环。

## step_level（type: custom, name: step_level）— 步骤级评估

```yaml
- type: custom
  name: step_level
  config:
    expected_trace: ["fs_read", "fs_write", "bash"]   # 按索引对照的工具调用序列
```

从 spans 提取实际工具调用序列，逐位对照，报告**第一个错误步骤**，得分为 `正确步数 / 总步数`。

## metric（type: metric）— LLM 质量指标分发

```yaml
- type: metric
  name: answer_relevancy      # 或 name: metric + config.metric_name
  config:
    thresholds: { "0.8": 1.0 }   # 可选，逐指标阈值覆盖
```

从 EvalRunner 注入的指标注册表分发（`answer_relevancy` / `faithfulness` / `context_recall` / `context_precision` 等）；未注册的指标名得 0 分并注明原因。

## 评分聚合策略（task 级）

```yaml
score_strategy: hybrid      # all_pass | weighted | hybrid（默认）
score_threshold: 0.7
```

- `all_pass`：所有 grader 通过才通过
- `weighted`：加权平均分 ≥ threshold
- `hybrid`：`required` 评分器必须全部通过 **且** 加权平均 ≥ threshold

## 查看注册表

```bash
curl http://localhost:8000/api/eval/graders     # 或独立部署 /v1/graders
```

返回全部可用评分器（name / type / description）。
