"use client";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Label,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { fmtDelta, fmtScore } from "@/lib/format";
import { useCompare, useRuns } from "@/lib/queries";
import type { ComparisonResponse, TaskDelta } from "@/lib/types";
import { useState } from "react";

export default function ComparePage() {
  const { data: runsData } = useRuns(100);
  const runs = (runsData?.runs ?? []).filter((r) => r.summary != null);
  const [runIdA, setRunIdA] = useState("");
  const [runIdB, setRunIdB] = useState("");
  const compare = useCompare();

  const comparison = compare.data?.comparison ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">A/B 对比</h1>
        <p className="text-sm text-muted-foreground">
          选择两个已完成的 run,对比全局指标与逐任务 delta
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>选择 Runs</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label>Run A (基准)</Label>
              <RunSelect value={runIdA} onChange={setRunIdA} runs={runs} />
            </div>
            <div>
              <Label>Run B (对比)</Label>
              <RunSelect value={runIdB} onChange={setRunIdB} runs={runs} />
            </div>
          </div>
          <div>
            <Button
              disabled={!runIdA || !runIdB || runIdA === runIdB || compare.isPending}
              onClick={() => compare.mutate({ runIdA, runIdB })}
            >
              {compare.isPending ? "对比中…" : "对比"}
            </Button>
            {runIdA && runIdA === runIdB ? (
              <span className="ml-3 text-xs text-warning">两个 run 不能相同</span>
            ) : null}
          </div>
          {compare.isError ? (
            <div className="text-sm text-danger">
              对比失败: {String(compare.error)}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {comparison ? <ComparisonResult data={compare.data!} /> : null}
    </div>
  );
}

function RunSelect({
  value,
  onChange,
  runs,
}: {
  value: string;
  onChange: (v: string) => void;
  runs: Array<{ run_id: string; suite_name: string; status: string }>;
}) {
  return (
    <select
      className="mono h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">— 选择 run —</option>
      {runs.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {r.run_id} ({r.suite_name})
        </option>
      ))}
    </select>
  );
}

function ComparisonResult({ data }: { data: ComparisonResponse }) {
  const { comparison } = data;
  const globalRows: Array<{ label: string; a: number; b: number; delta: number }> = [
    {
      label: "平均分",
      a: comparison.avg_score.a,
      b: comparison.avg_score.b,
      delta: comparison.avg_score.delta,
    },
    ...Object.entries(comparison.pass_at_k).map(([k, v]) => ({
      label: `pass@${k}`,
      a: v.a,
      b: v.b,
      delta: v.delta,
    })),
    ...Object.entries(comparison.pass_power_k).map(([k, v]) => ({
      label: `pass^${k}`,
      a: v.a,
      b: v.b,
      delta: v.delta,
    })),
  ];

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>
            全局指标 <span className="mono text-xs text-muted-foreground">A → B (delta)</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>指标</Th>
                <Th>Run A</Th>
                <Th>Run B</Th>
                <Th>Delta</Th>
              </tr>
            </thead>
            <tbody>
              {globalRows.map((row) => (
                <tr key={row.label}>
                  <Td>{row.label}</Td>
                  <Td>{fmtScore(row.a)}</Td>
                  <Td>{fmtScore(row.b)}</Td>
                  <Td>
                    <span
                      className={
                        row.delta > 0.1
                          ? "text-success"
                          : row.delta < -0.1
                            ? "text-danger"
                            : "text-muted-foreground"
                      }
                    >
                      {fmtDelta(row.delta)}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <DeltaListCard
          title={`退化任务 (${comparison.regressions.length})`}
          items={comparison.regressions}
          tone="danger"
        />
        <DeltaListCard
          title={`提升任务 (${comparison.improvements.length})`}
          items={comparison.improvements}
          tone="success"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>逐任务分数</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Task</Th>
                <Th>A</Th>
                <Th>B</Th>
                <Th>Delta</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(comparison.tasks).map(([taskId, v]) => (
                <tr key={taskId}>
                  <Td className="mono">{taskId}</Td>
                  <Td>{fmtScore(v.a)}</Td>
                  <Td>{fmtScore(v.b)}</Td>
                  <Td>
                    <span
                      className={
                        v.delta > 0.1
                          ? "text-success"
                          : v.delta < -0.1
                            ? "text-danger"
                            : "text-muted-foreground"
                      }
                    >
                      {fmtDelta(v.delta)}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function DeltaListCard({
  title,
  items,
  tone,
}: {
  title: string;
  items: TaskDelta[];
  tone: "danger" | "success";
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <div className="text-sm text-muted-foreground">无</div>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((d) => (
              <div
                key={d.task_id}
                className={`flex items-center justify-between rounded-lg border p-2 ${
                  tone === "danger" ? "border-danger/40 bg-danger/10" : "border-success/40 bg-success/10"
                }`}
              >
                <span className="mono text-sm">{d.task_id}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  {fmtScore(d.a)} → {fmtScore(d.b)}
                  <Badge tone={tone}>{fmtDelta(d.delta)}</Badge>
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
