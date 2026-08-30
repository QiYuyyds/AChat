# Aeval

[English](./README.md) | [简体中文](./README.zh-CN.md)

**Aeval** 是一个由 OpenTelemetry trace 驱动的开源 AI Agent 评测框架。用 YAML 声明评测套件，让 Agent 反复执行（重试 / 并发 / 超时隔离），逐 trial 评分（内置或自定义 Grader），最终聚合为统计上有意义的指标 —— `pass@k` / `pass^k` / 一致性 / 饱和度。

## 特性

- **Suite 即 YAML** — 任务、评分器、trial 数与阈值集中在一个声明式文件里，严格校验（semver、task id 唯一）。
- **9 个内置 Grader** — 确定性代码检查、LLM-as-Judge、环境状态检查、工具调用验证、转录分析、产物检查、人工评分（pending 语义）、步骤级评估、指标分发。
- **统计上严谨的聚合** — `pass@k`（能力）与 `pass^k`（可靠性），`k > n` 时二项外推，附一致性与饱和度检测。
- **全组件可插拔** — AgentRunner / TraceProvider / Storage / Environment / Grader 都是小型协议，实现即接入。
- **REST API 与 SSE** — 可挂载进任意 FastAPI 应用，也可独立服务（`/v1` 前缀），支持运行事件流。
- **CLI** — `eval-suite run / validate / list / show / compare / serve`。
- **数据集与 LLM 指标** — 从 trace 构建数据集、回填套件，并用 RAG 质量指标（answer relevancy / faithfulness / context recall·precision）给输出打分。

## 安装

```bash
pip install agent-eval            # 核心（编排 / 评分 / 存储）
pip install "agent-eval[api]"     # + REST API 服务
pip install "agent-eval[cli]"     # + eval-suite 命令行
```

导出 trace 到 [Arize Phoenix](https://github.com/Arize-ai/phoenix) 是可选能力：自行安装 `arize-phoenix`，Aeval 会在使用时懒加载。

## 快速开始

`examples/minimal` 使用内置 Mock Agent，完全离线可跑：

```bash
pip install "agent-eval[cli]"
eval-suite run examples/minimal/suite.yaml
eval-suite list runs
eval-suite show <run_id>
```

套件长这样：

```yaml
name: my-first-suite
version: 1.0.0
tasks:
  - id: simple-qa
    prompt: What files are in the workspace?
    max_trials: 3
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "files"
              target: transcript
```

通过 `eval-suite run --runner` 选项或 `agent_eval.runners` entry-point 注册你自己的 Agent（HTTP 适配示例见 `examples/achat`）。

## Dashboard

Next.js 前端位于 [`apps/dashboard`](./apps/dashboard)（总览 / Suite 管理 / SSE 实时 Run 报告 / Trial 下钻 / A/B 对比）。

## 文档

- [快速开始](./docs/getting-started.md)
- [接入指南](./docs/integration-guide.md)
- [Grader 参考](./docs/grader-reference.md)
- [YAML 格式](./docs/yaml-format.md)
- [CLI 参考](./docs/cli-reference.md)
- [架构](./docs/architecture.md)

## 许可

[MIT](./LICENSE)
