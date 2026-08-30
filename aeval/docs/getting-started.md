# 快速开始

本指南带你从零跑通 Aeval：安装 → 运行离线示例 → 查看结果 → 对比两次运行。

## 1. 安装

```bash
pip install agent-eval            # 核心（编排 / 评分 / 存储）
pip install "agent-eval[cli]"     # + eval-suite 命令行
pip install "agent-eval[api]"     # + REST API 服务（挂载或独立部署时）
```

要求 Python ≥ 3.11。trace 导出到 Arize Phoenix 是可选能力，按需自行 `pip install arize-phoenix`（框架内部懒加载导入）。

## 2. 运行离线示例

仓库自带一个完全离线的最小示例（Mock Agent，无需任何服务）：

```bash
git clone https://github.com/agent-eval/agent-eval
cd agent-eval

eval-suite run examples/minimal/suite.yaml
```

输出形如：

```
Starting eval run: aeval-minimal v1.0.0 (2 tasks, up to 3 trials each)
  [1/2] echo-qa: 3/3 trials passed
  [2/2] artifact-check: 3/3 trials passed
────────────────────────────────────────────────────────
Results Summary
────────────────────────────────────────────────────────
  Run: run_xxxxxxxxxxxx  Status: completed
  Pass@1:  100.0%
  Pass^1:  100.0%
  Avg Score: 1.0000
  Tasks: 2  Trials: 6
────────────────────────────────────────────────────────
```

## 3. 查看结果

结果默认落库到 `./aeval.db`（SQLite）：

```bash
eval-suite list runs                  # 运行历史
eval-suite show run_xxxxxxxxxxxx      # 单次运行详情
eval-suite show run_xxx --task t_ok   # 下钻单个任务（逐 trial + grader 明细）
eval-suite list suites                # 已运行过的套件清单
```

## 4. 对比两次运行

改坏一点东西再跑一次，然后 A/B 对比：

```bash
eval-suite compare run_aaa111 run_bbb222
```

输出 pass@k / pass^k / 平均分的 delta，以及退化（regressions）与提升（improvements）任务清单 —— 与 REST API `POST /compare` 同一语义。

## 5. 校验套件（CI 友好）

```bash
eval-suite validate examples/minimal/suite.yaml   # 合法 → 退出码 0
```

`validate` 只做加载与校验（semver、task id 唯一、grader 配置格式等），不执行 Agent，适合放进 CI 在运行前先挡住格式错误。

## 6. 接入你自己的 Agent

内置 Mock Agent 只用于演示。接入真实 Agent 只需实现一个方法（`AgentRunner` 协议）：

```python
class MyAgentRunner:
    async def run(self, task) -> tuple[str, list[dict], dict]:
        # 执行 task.prompt，收集 trace_id / transcript / outcome
        return trace_id, transcript, outcome
```

详细见 [接入指南](./integration-guide.md)；HTTP Agent 的适配模板见 `examples/achat/`。

## 下一步

- [YAML 格式](./yaml-format.md) — 套件怎么写
- [Grader 参考](./grader-reference.md) — 9 个内置评分器
- [CLI 参考](./cli-reference.md) — 全部命令与选项
- [架构](./architecture.md) — 模块与数据流
