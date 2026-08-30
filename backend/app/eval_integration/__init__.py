"""AChat 接入层 (change: add-aeval-integration-dashboard).

将 AChat Agent 系统适配为 Aeval 的 AgentRunner 契约。所有接触 AChat
内部 (`app.*`) 的代码集中于此层 — agent_eval 框架核心保持零反向依赖
(§15.1 规则 1)。

模块:
    - errors        错误类型 (装配/HTTP/run 失败)
    - client        AChat HTTP API 客户端 (认证/会话/消息/fs/artifacts)
    - trace_bridge  进程内 run_id → trace_id 桥 (OTel SpanProcessor)
    - runner        AChatAgentRunner + WorkspaceCoordinator
    - environment   AChatWorkspaceEnvironment (per-trial workspace 隔离)
    - graders       AChat 特定评分器 (achat_artifact / achat_dispatch)
    - config        create_aeval_runner() 装配入口
"""

from app.eval_integration.errors import (
    AChatApiError,
    AgentRunError,
    EvalConfigError,
)
from app.eval_integration.runner import AChatAgentRunner

__all__ = [
    "AChatAgentRunner",
    "AChatApiError",
    "AgentRunError",
    "EvalConfigError",
]
