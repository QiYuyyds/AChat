# AChat-style HTTP agent example

把 **HTTP Agent** 接入 Aeval 的适配模式（`http_agent_runner.py`）。这是从 AChat 仓库 `AChatAgentRunner` 形态通用化而来的模板 —— 完整接线（workspace 沙箱、事件总线完成检测、trace_id 桥、env 种子注入）见 AChat repo（`backend/app/eval_integration/`）。

## 前提

你的 Agent 暴露一个最小 HTTP 评测契约（本模板假设）：

- `POST /agent/run` `{"prompt", "env"}` → `{"run_id"}`
- `GET /agent/runs/{run_id}` → `{"status", "transcript", "outcome", "trace_id"}`

## 运行

```bash
export AEVAL_AGENT_URL=http://127.0.0.1:8000   # 你的 Agent 服务地址
eval-suite run examples/achat/suite.yaml --runner http-agent
```

`--runner http-agent` 经 `agent_eval.runners` entry-point 解析；把本仓库以 editable 方式安装（`pip install -e packages/agent-eval[cli]`）后，`pyproject.toml` 中声明：

```toml
[project.entry-points."agent_eval.runners"]
http-agent = "examples.achat.http_agent_runner:create_runner"
```

（未注册 entry-point 时也可在 Python 侧直接构造 `HTTPAgentRunner`，再交给 `EvalRunner`。）

## 说明

- 网络超时/抖动包装为 `TransientError` → 框架按指数退避自动重试；其余异常直接判该 trial 失败
- `task.env`（如种子文件）原样透传给 Agent 端点
- `trace_id` 可选 —— 传空字符串则跳过 Phoenix 关联
