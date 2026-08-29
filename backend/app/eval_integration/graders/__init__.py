"""AChat 特定评分器 (任务 2.3)。

均实现通用 Grader 契约, 仅注册于 AChat 接入装配 — 框架核心不感知。
"""

from eval_integration.graders.artifact import AChatArtifactGrader
from eval_integration.graders.dispatch import AChatDispatchGrader

__all__ = [
    "AChatArtifactGrader",
    "AChatDispatchGrader",
]
