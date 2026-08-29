"use client";

import { RunsTable } from "@/components/runs-table";
import { ScoreTrend } from "@/components/score-trend";
import { StatCard } from "@/components/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { buildScoreTrend, useOverviewStats } from "@/lib/queries";
import { fmtPct, fmtScore } from "@/lib/format";

export default function OverviewPage() {
  const { stats, loading, runs } = useOverviewStats();
  const trend = buildScoreTrend(runs);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">总览</h1>
        <p className="text-sm text-muted-foreground">Aeval 评测运行全局状态</p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Suites" value={loading ? "…" : String(stats!.suites)} href="/suites" />
        <StatCard label="Tasks" value={loading ? "…" : String(stats!.tasks)} />
        <StatCard label="Runs" value={loading ? "…" : String(stats!.runs)} />
        <StatCard
          label="平均分"
          value={loading ? "…" : fmtScore(stats!.avgScore)}
          hint="已完成的 runs"
        />
        <StatCard
          label="pass@3"
          value={loading ? "…" : fmtPct(stats!.passAt3)}
          hint="已完成 runs 均值"
        />
      </div>

      <ScoreTrend points={trend} />

      <Card>
        <CardHeader>
          <CardTitle>最近运行</CardTitle>
        </CardHeader>
        <CardContent>
          <RunsTable runs={runs} />
        </CardContent>
      </Card>
    </div>
  );
}
