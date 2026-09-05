"""InfrastructureStatus — aggregated health of all optional infrastructure services.

rag-infra-config: adds per-service config source (db/env/none) + detail
(失败原因 / 未启用原因) so GET /api/infra/status can explain "改了不生效"
and "为什么降级"。legacy summary() / dashboard_lines() 输出格式不变（启动
日志面板行为保留）。
"""

from dataclasses import dataclass, field


@dataclass
class ServiceSnapshot:
    """One service's startup-time observable state (rag-infra-config)."""

    status: str = "disabled"  # connected / degraded / disabled
    config_source: str = "none"  # db / env / none
    detail: str | None = None  # degraded → 失败原因; disabled → 未启用原因


@dataclass
class InfrastructureStatus:
    """Health snapshot of all optional infrastructure backends."""

    postgres: str = "disconnected"
    milvus: str = "disconnected"
    neo4j: str = "disconnected"
    kafka: str = "disconnected"
    redis: str = "disconnected"

    # rag-infra-config observability — written by the factory at startup,
    # exposed via service_states() (startup panel keeps using summary()).
    milvus_snap: ServiceSnapshot = field(default_factory=ServiceSnapshot)
    neo4j_snap: ServiceSnapshot = field(default_factory=ServiceSnapshot)
    kafka_snap: ServiceSnapshot = field(default_factory=ServiceSnapshot)
    embedding_snap: ServiceSnapshot = field(default_factory=ServiceSnapshot)
    phoenix_snap: ServiceSnapshot = field(default_factory=ServiceSnapshot)

    def summary(self) -> dict[str, str]:
        return {
            "postgres": self.postgres,
            "milvus": self.milvus,
            "neo4j": self.neo4j,
            "kafka": self.kafka,
            "redis": self.redis,
        }

    def is_any_connected(self) -> bool:
        return any(
            v == "connected"
            for k, v in self.summary().items()
            if k != "postgres"
        )

    def dashboard_lines(self) -> list:
        """Return formatted status lines for startup dashboard log."""
        lines = ["=== Infrastructure Dashboard ==="]
        for name, status in self.summary().items():
            icon = "✅" if status == "connected" else "⬚"
            lines.append(f"  {icon} {name}: {status}")
        lines.append("==============================")
        return lines

    def service_states(self) -> dict[str, dict[str, str | None]]:
        """Per-service state for GET /api/infra/status (shared collector).

        Startup panel keeps dashboard_lines(); the HTTP endpoint shares the
        snapshots the factory records here. status: connected / degraded /
        disabled; configSource: db / env / none; detail: 失败或未启用原因.
        """
        states: dict[str, dict[str, str | None]] = {
            "postgres": {
                "status": "connected" if self.postgres == "connected" else "degraded",
                "configSource": "env",
                "detail": None if self.postgres == "connected" else self.postgres,
            },
            "redis": {
                "status": "disabled",
                "configSource": "none",
                "detail": "removed (dual-DB migration)",
            },
        }
        for name, snap in (
            ("milvus", self.milvus_snap),
            ("neo4j", self.neo4j_snap),
            ("kafka", self.kafka_snap),
            ("embedding", self.embedding_snap),
            ("phoenix", self.phoenix_snap),
        ):
            states[name] = {
                "status": snap.status,
                "configSource": snap.config_source,
                "detail": snap.detail,
            }
        return states
