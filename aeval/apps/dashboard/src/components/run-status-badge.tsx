"use client";

import { Badge } from "@/components/ui/primitives";
import type { RunStatus } from "@/lib/types";

const TONES: Record<RunStatus, "success" | "danger" | "warning" | "primary" | "muted"> = {
  completed: "success",
  failed: "danger",
  cancelled: "warning",
  running: "primary",
  pending: "muted",
};

const LABELS: Record<RunStatus, string> = {
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  pending: "等待中",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge tone={TONES[status]}>
      {status === "running" && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />}
      {LABELS[status]}
    </Badge>
  );
}
