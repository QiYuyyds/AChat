# Minimal example

最简单的 Aeval 接入示例：内置 Mock Agent + 两个任务的 toy suite，**完全离线可跑，零外部依赖**（除 `agent-eval` 本身）。

## 运行

方式一 — CLI（`pip install "agent-eval[cli]"` 后）：

```bash
eval-suite run examples/minimal/suite.yaml
eval-suite list runs
eval-suite show <run_id>
```

方式二 — 纯 Python API：

```bash
python examples/minimal/runner.py
```

两种方式的结果都落库到 `./aeval.db`，可用 `eval-suite show` 复查。

## 这个例子展示了什么

- `suite.yaml`：任务 / 评分器 / trial 数的声明式定义（详见 [YAML 格式](../../docs/yaml-format.md)）
- `code_based` 确定性评分器（`contains` 检查）
- `artifact_check` 产物检查评分器
- 结果持久化（SQLite）与 pass@k / pass^k 汇总

## 下一步

- 想接自己的 Agent？看 [examples/achat](../achat/)（HTTP 适配模式）与 [接入指南](../../docs/integration-guide.md)
- 想了解所有内置评分器？看 [Grader 参考](../../docs/grader-reference.md)
