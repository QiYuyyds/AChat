"""InfrastructureStatus — aggregated health of all optional infrastructure services."""

from dataclasses import dataclass
from typing import Dict


@dataclass
class InfrastructureStatus:
    """Health snapshot of all optional infrastructure backends."""

    postgres: str = "disconnected"
    milvus: str = "disconnected"
    elasticsearch: str = "disconnected"
    neo4j: str = "disconnected"
    kafka: str = "disconnected"

    def summary(self) -> Dict[str, str]:
        return {
            "postgres": self.postgres,
            "milvus": self.milvus,
            "elasticsearch": self.elasticsearch,
            "neo4j": self.neo4j,
            "kafka": self.kafka,
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
