"""Dataset semantic versioning — semver bump rules with a change log.

Rules (design §18.5.1):
- major: 破坏性变更 (删除条目、修改评分器)
- minor: 新增条目
- patch: 修正描述、调整阈值等不改变任务集合语义的修正

Every bump appends a change-record entry to the dataset's change_log so the
history is persisted with the dataset.
"""

from __future__ import annotations

from typing import Literal

from eval_harness.dataset.models import DatasetError, EvalDataset, now_ms

ChangeType = Literal["major", "minor", "patch"]


class DatasetVersionManager:
    """数据集版本管理 (无状态, 纯函数式更新)"""

    @staticmethod
    def parse_version(version: str) -> tuple[int, int, int]:
        parts = version.split(".")
        if len(parts) != 3:
            raise DatasetError(f"Invalid semver version '{version}' (expected major.minor.patch)")
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            raise DatasetError(f"Invalid semver version '{version}' (non-numeric part)") from None

    @classmethod
    def bump_version(cls, version: str, change_type: ChangeType) -> str:
        major, minor, patch = cls.parse_version(version)
        if change_type == "major":
            return f"{major + 1}.0.0"
        if change_type == "minor":
            return f"{major}.{minor + 1}.0"
        if change_type == "patch":
            return f"{major}.{minor}.{patch + 1}"
        raise DatasetError(
            f"Invalid change_type '{change_type}' (valid: major, minor, patch)"
        )

    @classmethod
    def bump(
        cls,
        dataset: EvalDataset,
        change_type: ChangeType,
        change_note: str = "",
    ) -> EvalDataset:
        """
        升版并记录变更。

        Returns:
            升版后的 dataset 副本 (version/updated_at/change_log 更新)
        """
        new_version = cls.bump_version(dataset.version, change_type)
        entry = {
            "version": new_version,
            "change_type": change_type,
            "note": change_note,
            "at": now_ms(),
            "item_count": len(dataset.items),
        }
        return dataset.model_copy(update={
            "version": new_version,
            "updated_at": entry["at"],
            "change_log": [*dataset.change_log, entry],
        })
