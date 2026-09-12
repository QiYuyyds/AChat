"""AChatWorkspaceEnvironment — per-trial workspace 隔离 + **评测侧取证探针**。

隔离模型 (设计文档 D2): 每 trial 由 AChatAgentRunner 新建独立 conversation
+ sandbox workspace — 天然隔离, 不复用、不清理旧 workspace。

本环境管理器在框架的 EnvironmentManager 协议 (snapshot → setup → … → probe →
teardown → verify_clean → restore) 上提供两件事:

1. **取证探针 (change ③)** —— 由框架在 teardown 之前调用, 在被评方写不动的
   通道上读环境。读数一律被框架钉成 ``harness`` 级, 于是「文件到底在不在」不再
   取决于 agent 怎么说:
     - ``workspace_files``: 有界递归清单 + 文件内容 (经 fs API 读实际 workspace)
     - ``db_dump``: 本地库里该会话的 messages / artifacts 行 (权威落库状态)
   没有进行中的 trial 会话、或某通道读失败时, **返回一条带原因的「没取到」读数**
   而不是空列表 —— 空读数会被下游读成「环境里确实没有」, 那是一个结论。

2. 防御性校验:
    - setup():     无操作 — 隔离由 runner 新建会话实现 (种子文件由 runner
                   在发送 prompt 前写入, 见 runner.run)
    - snapshot():  返回当前 trial 会话与种子后基线清单 (框架在 setup 前调用,
                   per-trial 新建模型下通常为空态)
    - teardown():  删除 trial 会话 (workspace/artifacts 级联清理); 末期清单
                   已由取证探针在停止前读到, 这里只兜底补一次
    - verify_clean(): 以种子后清单为基线比对差异; 核心防御 = 种子前清单必须
                   为空 (忽略 .git 等隐藏条目) — 非空说明 workspace 模式退化
                   为共享目录; 会话复用 (历史重复) 同样判不洁
    - restore():   兜底再删一次 trial 会话 (幂等)
"""

from __future__ import annotations

import logging
from typing import Any

from agent_eval.core.types import EvalTask, EvidenceKind, Observation, ObservedBy
from agent_eval.trace.observations import AbsentReason

from app.eval_integration.client import AChatApiClient
from app.eval_integration.runner import (
    PROBE_DB_DUMP,
    PROBE_WORKSPACE_FILES,
    WorkspaceCoordinator,
    collect_workspace_listing,
)

logger = logging.getLogger(__name__)

# 每 trial 读取的文件数上限 (取证正文体积的实际控制点)
_MAX_PROBE_FILES = 50
_MAX_FILE_BYTES = 200_000
# db_dump 每表行数上限
_MAX_DB_ROWS = 200


def _foreign_entries(listing: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """种子前清单中判定"非全新"的条目 — 顶层隐藏条目 (.git 等) 视为良性。"""
    return {
        path: info
        for path, info in listing.items()
        if not info.get("name", "").startswith(".")
    }


def _absent(channel: str, reason: str, detail: str) -> Observation:
    """一条「没取到」的读数 —— 绝不以空读数冒充「环境里确实没有」。"""
    return Observation.absent(
        EvidenceKind.STATE, reason, channel=channel, detail=detail
    )


async def collect_workspace_files(
    client: AChatApiClient, conversation_id: str
) -> dict[str, Any]:
    """取证通道读数: 实际 workspace 的清单 + (有界) 内容。

    这些内容来自 fs API 而不是 agent 的自述, 因此可以合法地标成 ``harness`` 级。
    """
    listing = await collect_workspace_listing(client, conversation_id)
    files: dict[str, str] = {}
    unreadable: list[str] = []
    for rel, info in sorted(listing.items()):
        if info.get("isDirectory") or len(files) >= _MAX_PROBE_FILES:
            continue
        if (info.get("size") or 0) > _MAX_FILE_BYTES:
            files[rel] = "(skipped: file too large)"
            continue
        try:
            data = await client.fs_read(conversation_id, rel)
            files[rel] = str(data.get("content", ""))
        except Exception as e:  # noqa: BLE001 - 单文件读失败记下来, 不冒充空
            unreadable.append(rel)
            files[rel] = f"(read failed: {e})"
    return {
        "files": files,
        "listing": listing,
        "unreadable": unreadable,
        "truncated": len(listing) >= _MAX_PROBE_FILES,
    }


async def collect_trial_db_dump(conversation_id: str) -> dict[str, Any]:
    """取证通道读数: 本地库里该 trial 会话的 messages / artifacts 行。

    ``state_check`` 的 ``db_record`` 期望以此为权威来源 (而不是 agent 说它写了什么)。
    """
    from sqlalchemy import select

    from app.db.engine import get_local_db
    from app.db.models import Artifact, Message

    async with get_local_db() as db:
        messages = (
            await db.execute(
                select(
                    Message.id, Message.role, Message.status, Message.run_id
                )
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at)
                .limit(_MAX_DB_ROWS)
            )
        ).all()
        artifacts = (
            await db.execute(
                select(Artifact.id, Artifact.type, Artifact.title, Artifact.version)
                .where(Artifact.conversation_id == conversation_id)
                .limit(_MAX_DB_ROWS)
            )
        ).all()
    return {
        "messages": [
            {"id": r[0], "role": r[1], "status": r[2], "run_id": r[3]} for r in messages
        ],
        "db_records": [
            {
                "table": "artifacts",
                "id": r[0],
                "type": r[1],
                "title": r[2],
                "version": r[3],
            }
            for r in artifacts
        ],
        "artifact_count": len(artifacts),
    }


class AChatWorkspaceEnvironment:
    """per-trial 新建 conversation 隔离 + 取证探针 + workspace 清单基线校验。"""

    def __init__(
        self,
        client: AChatApiClient,
        coordinator: WorkspaceCoordinator,
        *,
        delete_conversations: bool = True,
        db_probe_available: bool = True,
    ):
        """
        Args:
            client: 与 runner 共用的 AChat HTTP 客户端
            coordinator: 与 runner 共享的 trial 状态单元 (runner 发布
                conversation 与基线清单)
            delete_conversations: teardown 时是否删除 trial 会话
                (关闭则保留全部 trial 会话供人工检查, 工作区会累积)
            db_probe_available: 本机是否可查本地库 (独立脚本跑远程库未就位时关闭,
                该通道即按「没取到」归类而不是返回空记录)
        """
        self.client = client
        self.coordinator = coordinator
        self.delete_conversations = delete_conversations
        self.db_probe_available = db_probe_available
        # 已见过的 trial 会话 ID (检测会话复用; verify_clean / teardown 共同维护)
        self._trial_history: list[str] = []

    # ── EnvironmentManager 协议 ──────────────────────────────────────────

    async def setup(self, task: EvalTask) -> None:
        """trial 开始前 — 无操作 (隔离由 runner 新建会话实现)。"""
        return None

    async def probe(self, channel: str = "") -> list[Observation]:
        """评测侧独立取证 (框架在 teardown 之前调用, 也可在运行中被适配层触发)。

        空 channel 或框架的 ``end_state`` 通道 → 一次读全部通道: 这样「至少有一
        个结束态读数」由框架保证, 不依赖接入方是否记得调。
        """
        trial = self.coordinator.current
        if trial is None:
            return [
                _absent(
                    channel or "probe",
                    AbsentReason.PROVIDER_UNAVAILABLE.value,
                    "没有进行中的 trial 会话 (runner 未发布 conversation)",
                )
            ]

        wanted = (
            [PROBE_WORKSPACE_FILES, PROBE_DB_DUMP]
            if channel in ("", "end_state")
            else [channel]
        )
        readings: list[Observation] = []
        for name in wanted:
            try:
                if name == PROBE_WORKSPACE_FILES:
                    payload = await collect_workspace_files(self.client, trial.conversation_id)
                    # 顺手发布末期清单, 供 verify_clean 比对 (teardown 前)
                    trial.final_listing = payload["listing"]
                elif name == PROBE_DB_DUMP:
                    if not self.db_probe_available:
                        readings.append(
                            _absent(
                                name,
                                AbsentReason.PROVIDER_UNAVAILABLE.value,
                                "本地库不可用 (db_probe_available=False)",
                            )
                        )
                        continue
                    payload = await collect_trial_db_dump(trial.conversation_id)
                else:
                    readings.append(
                        _absent(
                            name,
                            AbsentReason.PROVIDER_NOT_COVERED.value,
                            f"未知取证通道 {name!r}; 可用: "
                            f"{PROBE_WORKSPACE_FILES} / {PROBE_DB_DUMP}",
                        )
                    )
                    continue
            except Exception as e:  # noqa: BLE001 - 探针故障是「没取到」, 不是「没有」
                logger.warning("evidence probe %s failed: %s", name, e)
                readings.append(
                    _absent(
                        name,
                        AbsentReason.PROVIDER_UNAVAILABLE.value,
                        f"{type(e).__name__}: {e}",
                    )
                )
                continue
            readings.append(
                Observation(
                    kind=EvidenceKind.STATE,
                    observed_by=ObservedBy.HARNESS,
                    channel=name,
                    value=payload,
                )
            )
        return readings

    async def teardown(self, task: EvalTask) -> None:
        """trial 结束后: 删除 trial 会话 (末期清单已由取证探针读到)。"""
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

    async def verify_clean(
        self,
        baseline: dict[str, Any],
        harness_readings: list[Observation] | None = None,
    ) -> dict[str, Any]:
        """校验 trial workspace 隔离性 (D2)。

        判定:
            1. 种子前清单 (忽略隐藏条目) 非空 → workspace 非全新, 判不洁
               (防御 workspace 模式退化为共享目录)
            2. 会话 ID 出现过一次以上 → 判不洁 (复用而非新建)
            3. 其余情形判洁; 种子后 → 末期的清单差异作为参考信息返回
               (Agent 产出文件属预期变更, 不影响 clean)

        ``harness_readings`` 是框架递来的评测侧取证读数: 末期清单以**独立观测**
        为准, 不再依赖被评方自报状态 (change ③)。缺席时退回 coordinator 里的值。
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

        end_listing = _probe_listing(harness_readings) or trial.final_listing
        if end_listing is not None:
            baseline_files = trial.post_seed_listing
            changed = sorted(
                path
                for path in set(baseline_files) | set(end_listing)
                if end_listing.get(path) != baseline_files.get(path)
            )
            differences.append(
                {
                    "kind": "trial_changes",
                    "detail": (
                        "workspace changes relative to the seed baseline (expected agent output)"
                    ),
                    "files": changed,
                    "source": "harness_probe" if _probe_listing(harness_readings) else "coordinator",
                },
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


def _probe_listing(readings: list[Observation] | None) -> dict[str, Any] | None:
    """从取证读数里取末期清单 (最后一次 workspace_files 读数的 listing)。"""
    found: dict[str, Any] | None = None
    for reading in readings or []:
        if reading.is_absent or reading.channel != PROBE_WORKSPACE_FILES:
            continue
        value = reading.value
        if isinstance(value, dict) and isinstance(value.get("listing"), dict):
            found = value["listing"]
    return found
