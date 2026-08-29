"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { CoverageReport } from "@/lib/types";

/** 能力维度覆盖度 — 横向条形展示 (0-1) */
export function CoverageBars({ report }: { report: CoverageReport | null }) {
  const entries = Object.entries(report?.coverage ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>能力覆盖度</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {!report ? <div className="text-sm text-muted-foreground">加载中…</div> : null}
        {report && entries.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            暂无能力标注 — 给条目的 metadata.capabilities 加维度标签后可见
          </div>
        ) : null}
        {entries.map(([dim, value]) => {
          const insufficient = (report?.insufficient ?? []).some((i) => i.capability === dim);
          return (
            <div key={dim} className="flex items-center gap-3">
              <span className="w-28 shrink-0 truncate text-sm">{dim}</span>
              <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={
                    insufficient
                      ? "h-full rounded-full bg-warning"
                      : "h-full rounded-full bg-primary"
                  }
                  style={{ width: `${Math.round(value * 100)}%` }}
                />
              </div>
              <span
                className={
                  insufficient
                    ? "w-12 shrink-0 text-right text-xs text-warning"
                    : "w-12 shrink-0 text-right text-xs text-muted-foreground"
                }
              >
                {Math.round(value * 100)}%
              </span>
            </div>
          );
        })}
        {report && report.untagged_items > 0 ? (
          <div className="text-xs text-muted-foreground">
            {report.untagged_items} 条未标注能力维度
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
