"""FileLifecycleManager — 文件生命周期状态机，管理 Document.status 和 Document.graph_status。

两个独立状态字段：
  - Document.status: 文件解析/索引状态
  - Document.graph_status: 图谱构建状态

使用乐观并发控制（UPDATE ... WHERE id=? AND status=?）。
非法状态转换被拒绝。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update

from app.db.engine import get_remote_db
from app.db.models import Document

logger = logging.getLogger(__name__)


class FileStatus:
    """Document.status 的 11 种状态常量。"""

    # 文件级状态
    UPLOADING = "uploading"       # 文件上传中
    PARSING = "parsing"           # 解析中
    PARSED = "parsed"             # 解析完成
    INDEXING = "indexing"         # RAG 索引中
    INDEXED = "indexed"           # RAG 索引完成
    ACTIVE = "active"             # 活跃（正常可用，向后兼容）
    ERROR = "error"               # 解析/索引错误
    DELETED = "deleted"           # 软删除
    ARCHIVED = "archived"         # 归档

    # 图谱状态（独立流转）
    GRAPH_PENDING = "graph_pending"
    GRAPH_BUILDING = "graph_building"
    GRAPH_INDEXED = "graph_indexed"
    ERROR_GRAPH = "error_graph"

    ALL_FILE_STATUSES = {
        UPLOADING, PARSING, PARSED, INDEXING, INDEXED,
        ACTIVE, ERROR, DELETED, ARCHIVED,
    }

    ALL_GRAPH_STATUSES = {
        GRAPH_PENDING, GRAPH_BUILDING, GRAPH_INDEXED, ERROR_GRAPH,
    }


class DocumentLifecycleManager:
    """管理 Document.status 和 Document.graph_status 的合法状态转换。

    TRANSITIONS 字典定义合法状态转换：
      key: 当前状态, value: {允许转换的目标状态集合}
    """

    # Document.status 合法转换
    TRANSITIONS: dict[str, set[str]] = {
        FileStatus.UPLOADING: {FileStatus.PARSING, FileStatus.ERROR, FileStatus.DELETED},
        FileStatus.PARSING: {FileStatus.PARSED, FileStatus.ERROR, FileStatus.DELETED},
        FileStatus.PARSED: {FileStatus.INDEXING, FileStatus.ACTIVE, FileStatus.ERROR, FileStatus.DELETED},
        FileStatus.INDEXING: {FileStatus.INDEXED, FileStatus.ERROR, FileStatus.DELETED},
        FileStatus.INDEXED: {FileStatus.ACTIVE, FileStatus.ARCHIVED, FileStatus.DELETED},
        FileStatus.ACTIVE: {FileStatus.PARSING, FileStatus.INDEXING, FileStatus.ARCHIVED, FileStatus.DELETED},
        FileStatus.ERROR: {FileStatus.PARSING, FileStatus.UPLOADING, FileStatus.DELETED},
        FileStatus.ARCHIVED: {FileStatus.ACTIVE, FileStatus.DELETED},
        FileStatus.DELETED: set(),  # 终态，不可转换
    }

    # Document.graph_status 合法转换
    GRAPH_TRANSITIONS: dict[str, set[str]] = {
        FileStatus.GRAPH_PENDING: {FileStatus.GRAPH_BUILDING, FileStatus.ERROR_GRAPH},
        FileStatus.GRAPH_BUILDING: {FileStatus.GRAPH_INDEXED, FileStatus.ERROR_GRAPH},
        FileStatus.GRAPH_INDEXED: {FileStatus.GRAPH_PENDING, FileStatus.ERROR_GRAPH},
        FileStatus.ERROR_GRAPH: {FileStatus.GRAPH_PENDING, FileStatus.GRAPH_BUILDING},
        None: {FileStatus.GRAPH_PENDING},  # 初始状态
    }

    @classmethod
    def is_valid_transition(cls, current: str | None, target: str) -> bool:
        """检查 Document.status 转换是否合法。"""
        allowed = cls.TRANSITIONS.get(current or "", set())
        return target in allowed

    @classmethod
    def is_valid_graph_transition(cls, current: str | None, target: str) -> bool:
        """检查 Document.graph_status 转换是否合法。"""
        allowed = cls.GRAPH_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    async def transition(
        cls,
        document_id: str,
        target: str,
        *,
        operator_id: str | None = None,
        is_graph: bool = False,
    ) -> dict:
        """乐观并发检查的状态转换。

        使用 UPDATE ... WHERE id=? AND status=? 实现乐观并发。
        如果当前状态不匹配（被其他操作修改），转换失败。

        Args:
            document_id: 文档 ID
            target: 目标状态
            operator_id: 操作者 ID（用于审计，可选）
            is_graph: True 操作 graph_status，False 操作 status

        Returns:
            dict: {"success": bool, "previous": str, "current": str, "message": str}
        """
        async with get_remote_db() as session:
            # 读取当前状态
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                return {
                    "success": False,
                    "previous": "",
                    "current": "",
                    "message": f"Document not found: {document_id}",
                }

            current = doc.graph_status if is_graph else doc.status

            # 检查转换合法性
            valid = (
                cls.is_valid_graph_transition(current, target)
                if is_graph
                else cls.is_valid_transition(current, target)
            )
            if not valid:
                field = "graph_status" if is_graph else "status"
                return {
                    "success": False,
                    "previous": current or "",
                    "current": current or "",
                    "message": f"Invalid {field} transition: {current} → {target}",
                }

            # 乐观并发更新
            field = "graph_status" if is_graph else "status"
            now = datetime.utcnow().timestamp()

            update_stmt = (
                update(Document)
                .where(Document.id == document_id)
            )
            if is_graph:
                update_stmt = update_stmt.where(Document.graph_status == current).values(
                    graph_status=target, updated_at=now
                )
            else:
                update_stmt = update_stmt.where(Document.status == current).values(
                    status=target, updated_at=now
                )

            result = await session.execute(update_stmt)
            affected = result.rowcount or 0

            if affected == 0:
                # 并发冲突：当前状态已被其他操作修改
                return {
                    "success": False,
                    "previous": current or "",
                    "current": "",
                    "message": "Optimistic concurrency conflict: state changed by another operation",
                }

            logger.info(
                "DocumentLifecycle: %s transitioned %s → %s (operator=%s)",
                document_id, current, target, operator_id,
            )
            return {
                "success": True,
                "previous": current or "",
                "current": target,
                "message": "Transition successful",
            }
