"""Stats ingest payload schemas (usage-stats capability).

隐私边界在模型层强制（design D2）：指标名用 Literal 白名单钉死、字段集封闭
（extra="forbid"）——内容 / token / 自由文本字段在载荷校验层即被 422 拒绝，
不存在任何可落库的内容路径。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 指标白名单（spec 硬边界）：仅四个计数指标，不含 token 用量 / 内容
StatMetric = Literal["logins", "active_minutes", "sessions_created", "messages_sent"]
StatClientType = Literal["web", "desktop"]


class StatsBatchEntry(BaseModel):
    """One aggregated counter: user × day × client_type × metric → value."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    day: str = Field(description="UTC date (YYYY-MM-DD)")
    client_type: StatClientType = Field(alias="clientType")
    metric: StatMetric
    value: int = Field(ge=1, description="positive count increment")

    @field_validator("day")
    @classmethod
    def _day_is_real_date(cls, v: str) -> str:
        try:
            parsed = date.fromisoformat(v)
        except ValueError as e:
            raise ValueError("day must be a valid date in YYYY-MM-DD") from e
        if parsed.isoformat() != v:
            raise ValueError("day must be a valid date in YYYY-MM-DD")
        return v


class StatsBatchRequest(BaseModel):
    """POST /api/stats/batch payload. nonce 仅供日志排障（design: 重复计数接受近似误差）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    entries: list[StatsBatchEntry] = Field(min_length=1, max_length=1000)
    nonce: str | None = None


class StatsHeartbeatRequest(BaseModel):
    """POST /api/stats/heartbeat payload — 前台活跃心跳（design D6）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    interval_minutes: int = Field(alias="intervalMinutes", ge=1)
