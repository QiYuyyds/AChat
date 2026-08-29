"use client";

import { ScoreTrend } from "@/components/score-trend";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { fmtScore, fmtTime } from "@/lib/format";
import { useTask, useTaskHistory } from "@/lib/queries";
import type { TrendPoint } from "@/lib/queries";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = decodeURIComponent(params.id);
  const { data: taskResp, isLoading, error } = useTask(taskId);
  const { data: historyResp, error: historyError } = useTaskHistory(taskId);

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error || !taskResp) {
    return <div className="text-sm text-danger">任务不存在或后端未连接 ({String(error)})</div>;
  }

  const task = taskResp.task;
  const history = historyResp?.history ?? [];
  // ScoreTrend 期望时间升序
  const trendPoints: TrendPoint[] = [...history]
    .reverse()
    .map((h) => ({ time: h.started_at, score: h.avg_score, runId: h.run_id }));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mono text-xl font-semibold">{task.id}</h1>
          <p className="text-sm text-muted-foreground">{task.description || "—"}</p>
        </div>
        <Link href={`/suites/${encodeURIComponent(taskResp.suite_name)}`}>
          <Button variant="outline">Suite: {taskResp.suite_name} →</Button>
        </Link>
      </div>

      {/* 完整定义 */}
      <Card>
        <CardHeader>
          <CardTitle>任务定义</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 text-sm">
          <div className="flex flex-col gap-1">
            <div className="text-xs font-semibold text-muted-foreground">Prompt</div>
            <pre className="mono whitespace-pre-wrap rounded-lg border border-border bg-muted/30 p-3 text-xs">
              {task.prompt}
            </pre>
          </div>

          <div className="flex flex-col gap-1">
            <div className="text-xs font-semibold text-muted-foreground">
              Graders ({task.graders.length})
            </div>
            <div className="flex flex-col gap-1">
              {task.graders.map((g) => (
                <div
                  key={g.name}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs"
                >
                  <Badge tone="primary">{g.name}</Badge>
                  <span className="mono text-muted-foreground">{g.type}</span>
                  {g.required ? <Badge tone="danger">required</Badge> : null}
                  <span className="text-muted-foreground">weight {g.weight}</span>
                  {Object.keys(g.config).length > 0 ? (
                    <span className="mono line-clamp-1 text-muted-foreground">
                      {JSON.stringify(g.config)}
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-6">
            <div>
              <div className="text-xs font-semibold text-muted-foreground">评分策略</div>
              <div className="mono text-sm">{task.score_strategy}</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-muted-foreground">通过阈值</div>
              <div className="mono text-sm">{task.score_threshold}</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-muted-foreground">Max Trials</div>
              <div className="mono text-sm">{task.max_trials}</div>
            </div>
            <div className="min-w-40 flex-1">
              <div className="text-xs font-semibold text-muted-foreground">Env</div>
              <div className="mono line-clamp-2 text-xs text-muted-foreground">
                {Object.keys(task.env).length > 0 ? JSON.stringify(task.env) : "—"}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 历史结果 */}
      <ScoreTrend points={trendPoints} />

      <Card>
        <CardHeader>
          <CardTitle>历史结果 ({history.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {historyError ? (
            <div className="text-sm text-danger">历史加载失败: {String(historyError)}</div>
          ) : null}
          {history.length === 0 && !historyError ? (
            <div className="text-sm text-muted-foreground">
              该任务还没有参与过任何 run — 从所属 Suite 页面启动一次运行
            </div>
          ) : null}
          {history.length > 0 ? (
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>时间</Th>
                  <Th>通过</Th>
                  <Th>平均分</Th>
                  <Th>Grader 均分</Th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.run_id} className="hover:bg-muted/40">
                    <Td className="mono">
                      <Link
                        href={`/runs/${h.run_id}`}
                        className="text-primary hover:underline"
                      >
                        {h.run_id}
                      </Link>
                    </Td>
                    <Td className="text-muted-foreground">{fmtTime(h.started_at)}</Td>
                    <Td>
                      {h.trials_passed}/{h.trials_total}
                    </Td>
                    <Td>{fmtScore(h.avg_score)}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(h.graders).map(([name, score]) => (
                          <Badge key={name} tone={score >= 0.7 ? "success" : "danger"}>
                            {name}: {fmtScore(score)}
                          </Badge>
                        ))}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
