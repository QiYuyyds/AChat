"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import type { TrendPoint } from "@/lib/queries";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export function ScoreTrend({ points }: { points: TrendPoint[] }) {
  if (points.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>分数趋势</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">暂无已完成 run</CardContent>
      </Card>
    );
  }

  const data = points.map((p, i) => ({ idx: i + 1, score: p.score, runId: p.runId }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>分数趋势 (最近 {data.length} 次运行)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
              <XAxis dataKey="idx" stroke="var(--muted-foreground)" fontSize={11} />
              <YAxis domain={[0, 1]} stroke="var(--muted-foreground)" fontSize={11} />
              <Tooltip
                contentStyle={{
                  background: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(_, payload) => {
                  const runId = payload?.[0]?.payload?.runId;
                  return runId ? `run: ${runId}` : "";
                }}
              />
              <Line
                type="monotone"
                dataKey="score"
                stroke="var(--primary)"
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
