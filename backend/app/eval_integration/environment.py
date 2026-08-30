"""AChatWorkspaceEnvironment — per-trial workspace 隔离 (任务 2.2, 落定 §17.5)。

隔离模型 (设计文档 D2): 每 trial 由 AChatAgentRunner 新建独立 conversation
+ sandbox workspace — 天然隔离, 不复用、不清理旧 workspace。

本环境管理器在框架的 EnvironmentManager 协议 (snapshot → setup → … →
teardown → verify_clean → restore) 上提供防御性校验:

    - setup():     无操作 — 隔离由 runner 新建会话实现 (种子文件由 runner
                   在发送 prompt 前写入, 见 runner.run)
    - snapshot():  返回当前 trial 会话与种子后基线清单 (框架在 setup 前调用,
                   per-trial 新建模型下通常为空态)
    - teardown():  捕获 trial 末期清单后删除 trial 会话 (workspace/artifacts
                   级联清理); runner 已在此之前收集完 transcript/outcome
    - verify_clean(): 以种子后清单为基线比对差异; 核心防御 = 种子前清单必须
                   为空 (忽略 .git 等隐藏条目) — 非空说明 workspace 模式退化
                   为共享目录; 会话复用 (历史重复) 同样判不洁
    - restore():   兜底再删一次 trial 会话 (幂等)
"""

from __future__ import annotations

import logging
from typing import Any

from agent_eval.core.types import EvalTask

from app.eval_integration.client import AChatApiClient
from app.eval_integration.runner import WorkspaceCoordinator, collect_workspace_listing

logger = logging.getLogger(__name__)


def _foreign_entries(listing: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """种子前清单中判定"非全新"的条目 — 顶层隐藏条目 (.git 等) 视为良性。"""
    return {
        path: info
        for path, info in listing.items()
        if not info.get("name", "").startswith(".")
    }


class AChatWorkspaceEnvironment:
    """per-trial 新建 conversation 隔离 + workspace 清单基线校验。"""

    def __init__(
        self,
        client: AChatApiClient,
        coordinator: WorkspaceCoordinator,
        *,
        delete_conversations: bool = True,
    ):
        """
        Args:
            client: 与 runner 共用的 AChat HTTP 客户端
            coordinator: 与 runner 共享的 trial 状态单元 (runner 发布
                conversation 与基线清单)
            delete_conversations: teardown 时是否删除 trial 会话
                (关闭则保留全部 trial 会话供人工检查, 工作区会累积)
        """
        self.client = client
        self.coordinator = coordinator
        self.delete_conversations = delete_conversations
        # 已见过的 trial 会话 ID (检测会话复用; verify_clean / teardown 共同维护)
        self._trial_history: list[str] = []

    # ── EnvironmentManager 协议 ──────────────────────────────────────────

    async def setup(self, task: EvalTask) -> None:
        """trial 开始前 — 无操作 (隔离由 runner 新建会话实现)。"""
        return None

    async def teardown(self, task: EvalTask) -> None:
        """trial 结束后: 记录末期清单并删除 trial 会话。"""
        trial = self.coordinator.current
        if trial is None:
            return
        if trial.final_listing is None:
            try:
                trial.final_listing = await collect_workspace_listing(
                    self.client, trial.conversation_id
                )
            except Exception as e:  # noqa: BLE001 - 列目录失败不阻断清理
                logger.warning(
                    "teardown: final listing failed for %s: %s",
                    trial.conversation_id, e,
                )
                trial.final_listing = {}
        self._trial_history.append(trial.conversation_id)
        if self.delete_conversations:
            try:
                await self.client.delete_conversation(trial.conversation_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "teardown: delete %s failed: %s", trial.conversation_id, e
                )
        self.coordinator.clear(deleted=self.delete_conversations)

    async def snapshot(self) -> dict[str, Any]:
        """环境基线快照 (框架在 setup 前调用; per-trial 新建模型下为空态)。"""
        trial = self.coordinator.current
        return {
            "conversation_id": trial.conversation_id if trial else None,
            "files": dict(trial.post_seed_listing) if trial else {},
        }

    async def verify_clean(self, baseline: dict[str, Any]) -> dict[str, Any]:
        """校验 trial workspace 隔离性 (D2)。

        判定:
            1. 种子前清单 (忽略隐藏条目) 非空 → workspace 非全新, 判不洁
               (防御 workspace 模式退化为共享目录)
            2. 会话 ID 出现过一次以上 → 判不洁 (复用而非新建)
            3. 其余情形判洁; 种子后 → 末期的清单差异作为参考信息返回
               (Agent 产出文件属预期变更, 不影响 clean)
        """
        trial = self.coordinator.last or self.coordinator.current
        if trial is None:
            return {"clean": True, "differences": []}

        differences: list[dict[str, Any]] = []
        clean = True

        pre_seed = _foreign_entries(trial.pre_seed_files)
        if pre_seed:
            clean = False
            differences.append(
                {
                    "kind": "foreign_files",
                    "detail": (
                        "trial workspace was not empty before seeding — workspace "
                        "isolation may have degraded to a shared directory"
                    ),
                    "files": sorted(pre_seed),
                }
            )

        if self._trial_history.count(trial.conversation_id) > 1:
            clean = False
            differences.append(
                {
                    "kind": "reused_conversation",
                    "detail": "conversation was reused across trials instead of being created fresh",
                    "conversation_id": trial.conversation_id,
                }
            )

        if trial.final_listing is not None:
            baseline_files = trial.post_seed_listing
            changed = sorted(
                path
                for path in set(baseline_files) | set(trial.final_listing)
                if trial.final_listing.get(path) != baseline_files.get(path)
            )
            differences.append(
                {
                    "kind": "trial_changes",
                    "detail": "workspace changes relative to the seed baseline (expected agent output)",
                    "files": changed,
                }
            )

        return {"clean": clean, "differences": differences}

    async def restore(self, baseline: dict[str, Any]) -> None:
        """恢复到基线 — per-trial 新建模型下 = 删除 trial 会话 (幂等)。"""
        trial = self.coordinator.last or self.coordinator.current
        if trial is None or trial.deleted:
            return
        try:
            await self.client.delete_conversation(trial.conversation_id)
            trial.deleted = True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "restore: delete %s failed: %s", trial.conversation_id, e
            )
