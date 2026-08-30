import { Badge } from "@/components/ui/primitives";

const SOURCE_TONE: Record<string, "muted" | "success" | "danger" | "warning" | "primary"> = {
  manual: "muted",
  trace_mining: "primary",
  llm_generated: "warning",
  adversarial: "danger",
  regression: "success",
};

/** 数据集条目来源类型徽章 (manual/trace_mining/llm_generated/adversarial/regression) */
export function SourceBadge({ type }: { type: string }) {
  return <Badge tone={SOURCE_TONE[type] ?? "muted"}>{type}</Badge>;
}
