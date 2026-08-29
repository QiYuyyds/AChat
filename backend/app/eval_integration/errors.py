"""AChat 接入层错误类型。

- EvalConfigError: 装配配置缺失/非法 (create_aeval_runner)
- AChatApiError:  AChat HTTP API 返回非 2xx
- AgentRunError:  AChat run 以 failed/aborted 结束、超时, 或 trace_id
                  通道不可用 — 框架侧按 trial 失败处理 (不重试)
"""

from __future__ import annotations


class EvalConfigError(Exception):
    """评测装配配置错误 — message 列出全部缺失项。"""


class AChatApiError(Exception):
    """AChat HTTP API 错误响应。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AgentRunError(Exception):
    """AChat run 未成功完成 (failed/aborted/超时/trace 缺失)。

    Attributes:
        run_ids: 本次 trial 涉及的 AChat run ID
        status:  观测到的终止状态 (failed / aborted / timeout / unknown)
        elapsed_ms: 从发送 prompt 到错误发生的耗时
    """

    def __init__(
        self,
        message: str,
        *,
        run_ids: list[str] | None = None,
        status: str = "unknown",
        elapsed_ms: float = 0.0,
    ):
        super().__init__(message)
        self.run_ids = run_ids or []
        self.status = status
        self.elapsed_ms = elapsed_ms
