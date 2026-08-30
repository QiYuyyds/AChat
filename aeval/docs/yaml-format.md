# YAML 格式

Suite 是 Aeval 的评测声明单元。一个 YAML 文件描述名称、版本与一组任务；加载时做**严格校验**（Pydantic v2），任何格式错误都会给出具体原因（`eval-suite validate` / `SuiteLoadError`）。

## 完整结构

```yaml
name: my-suite                # 必填, ≤128 字符, 非空
description: 套件描述           # 可选
version: 1.0.0                # 可选, semver 格式 (^\d+\.\d+\.\d+$)
metadata:                     # 可选, 自定义元数据 (任意 JSON)
  author: team-a
  purpose: regression-check

tasks:                        # 必填, 至少 1 个任务
  - id: simple-qa             # 必填, 套件内唯一 (重复 → 校验失败)
    description: 任务的人类可读说明
    prompt: |                 # 必填, 发给 Agent 的输入
      请回答: HTTP 的默认端口是多少?
    env: {}                   # 可选, 透传给 AgentRunner (如种子文件)
    max_trials: 3             # 可选, 默认 3 (≥1)
    score_strategy: hybrid    # 可选: all_pass | weighted | hybrid (默认)
    score_threshold: 0.7      # 可选, 0.0-1.0
    tracked_metrics:          # 可选, 从 trace 提取的过程指标
      - n_turns
      - n_toolcalls
      - n_total_tokens
      - latency_ms
    graders:                  # 必填, 至少 1 个评分器 (见 grader-reference)
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              target: transcript
              value: "80"
```

## 校验规则一览

| 规则 | 失败表现 |
|------|----------|
| `name` 非空且 ≤128 字符 | ValidationError（含字段路径） |
| `version` 符合 semver | ValidationError |
| `tasks` 至少 1 个 | ValidationError |
| task `id` 套件内唯一 | `Duplicate task IDs: ['t1']` |
| 每个 task 至少 1 个 grader | ValidationError |
| grader `name` 格式 | ValidationError（正则约束） |
| `max_trials` ≥ 1、`sample_count` 1-10、`weight` ≥ 0 | ValidationError |
| `score_threshold` 0.0-1.0 | ValidationError |

文件不存在 / YAML 语法错误 / 顶层不是映射，都会包成带文件路径上下文的 `SuiteLoadError`。

## API 创建等价

同一套校验也适用于 API 的 JSON 创建（`POST /v1/suites` 或寄宿挂载 `POST /api/eval/suites`）—— YAML 加载与 API JSON 创建校验行为完全一致（校验器在 Pydantic 模型上，不落在解析路径里）。

## 多 trial 与统计

- `max_trials: N` 表示该任务的每个 task 跑 N 次 trial
- 汇总时计算 pass@k（k 次中至少一次成功）与 pass^k（k 次全部成功）
- `k > n`（要求次数多于实际 trial 数）时按二项分布外推
- 判定"任务通过"用的是 pass@1 语义（至少一次成功）之外的整体汇总，详见架构文档 §统计

## 版本化

`version` 是纯声明式 semver，用于结果对比与数据集升版工作流（`DatasetVersionManager`）。建议语义：

- patch：改 prompt 措辞/描述
- minor：增删任务
- major：改变任务语义或阈值（历史对比意义变化）

## 示例

- 最小离线示例：`examples/minimal/suite.yaml`
- HTTP Agent 接入示例：`examples/achat/suite.yaml`
- AChat 真实链路套件：AChat 仓库 `backend/eval_suites/first-suite.yaml`
