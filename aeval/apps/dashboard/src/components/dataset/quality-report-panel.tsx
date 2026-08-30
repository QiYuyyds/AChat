"use client";

import { Badge } from "@/components/ui/primitives";
import type { QualityReport } from "@/lib/types";

function IssueList({
  issues,
  tone,
  emptyText,
}: {
  issues: QualityReport["errors"];
  tone: "danger" | "warning";
  emptyText: string;
}) {
  if (issues.length === 0) {
    return <div className="text-xs text-muted-foreground">{emptyText}</div>;
  }
  return (
    <ul className="flex flex-col gap-1">
      {issues.map((issue, idx) => (
        <li
          key={`${issue.code}-${issue.item_id}-${idx}`}
          className="flex items-start gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
        >
          <Badge tone={tone}>{issue.code}</Badge>
          <span className="mono shrink-0">{issue.item_id || "—"}</span>
          <span className="text-muted-foreground">{issue.message}</span>
        </li>
      ))}
    </ul>
  );
}

/** 质量检查报告 — errors 阻塞 to-suite / warnings 仅提示 */
export function QualityReportPanel({ report }: { report: QualityReport | null }) {
  if (!report) return null;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        {report.ok ? (
          <Badge tone="success">通过 — 无阻塞错误</Badge>
        ) : (
          <Badge tone="danger">未通过 — {report.error_count} 个错误阻塞 to-suite</Badge>
        )}
        <span className="text-xs text-muted-foreground">
          共 {report.total_items} 条 · {report.warning_count} 个警告
        </span>
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold text-danger">错误 (error)</div>
        <IssueList
          issues={report.errors}
          tone="danger"
          emptyText="无错误"
        />
      </div>
      <div>
        <div className="mb-1 text-xs font-semibold text-warning">警告 (warning)</div>
        <IssueList
          issues={report.warnings}
          tone="warning"
          emptyText="无警告"
        />
      </div>
    </div>
  );
}
