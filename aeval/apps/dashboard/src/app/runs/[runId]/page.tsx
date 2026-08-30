"use client";

import { RunStatusBadge } from "@/components/run-status-badge";
import { StatCard } from "@/components/stat-card";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { fmtDuration, fmtPct, fmtScore, fmtTime } from "@/lib/format";
import { useRun, useRunTrials } from "@/lib/queries";
import { subscribeRunEvents } from "@/lib/sse";
import type { RunEvent, TrialFull, TrialLite } from "@/lib/types";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

export default function RunReportPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;
  const { data: run, isLoading, error, refetch } = useRun(runId);
  const { data: trialsData } = useRunTrials(runId);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  // 快照+增量协议: 挂载即拉全量快照 (useQuery),running 时订阅 SSE 增量;
  // 断线 onError → 失效快照查询强制重拉,再由 EventSource 自动重连
  const qcInvalidation = useRef<(() => void) | null>(null);

  const running = run?.status === "running" || run?.status === "pending";

  useEffect(() => {
    qcInvalidation.current = () => {
      void refetch();
    };
  }, [refetch]);

  useEffect(() => {
    if (!running) return;
    setEvents([]);
    const unsubscribe = subscribeRunEvents(runId, {
      onEvent: (event) => {
        setEvents((prev) => [...prev.slice(-99), event]);
        if (
          event.type === "task_complete" ||
          event.type === "run_complete" ||
          event.type === "error"
        ) {
          qcInvalidation.current?.();
        }
      },
      onError: () => {
        setConnected(false);
        qcInvalidation.current?.(); // 快照自愈
      },
    });
    setConnected(true);
    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [runId, running]);

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error || !run) {
    return <div className="text-sm text-danger">Run 不存在或后端未连接 ({String(error)})</div>;
  }

  const summary = run.summary;
  const taskIds = Object.keys(run.trials);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mono text-xl font-semibold">{run.run_id}</h1>
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            suite <Link className="text-primary hover:underline" href={`/suites/${encodeURIComponent(run.suite_name)}`}>{run.suite_name}</Link>
            · <RunStatusBadge status={run.status} />
            · {fmtTime(run.started_at)} · 耗时 {fmtDuration(run.duration_ms)}
          </p>
        </div>
        {running ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={connected ? "text-success" : "text-warning"}>
              {connected ? "● 实时更新已连接" : "○ 重连中 (快照恢复)"}
            </span>
          </div>
        ) : null}
      </div>

      {run.error ? (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          Run error: {run.error}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="pass@1" value={fmtScore(summary?.pass_at_k?.["1"])} />
        <StatCard label="pass@3" value={fmtScore(summary?.pass_at_k?.["3"] ?? summary?.pass_at_k?.["1"])} />
        <StatCard label="pass^3" value={fmtScore(summary?.pass_power_k?.["3"] ?? summary?.pass_power_k?.["1"])} />
        <StatCard label="平均分" value={fmtScore(summary?.avg_score)} />
        <StatCard
          label="进度"
          value={running ? `${taskProgressPct(events, run.trials)}%` : `${taskIds.length}/${summary?.total_tasks ?? taskIds.length}`}
          hint={running ? "实时 (SSE)" : "已完成"}
        />
      </div>

      {running ? <LiveEventFeed events={events} /> : null}

      <Card>
        <CardHeader>
          <CardTitle>任务结果</CardTitle>
        </CardHeader>
        <CardContent>
          <TaskResultsTable runId={run.run_id} taskIds={taskIds} trials={run.trials} />
        </CardContent>
      </Card>

      {!running ? <FailedTrialsCard runId={run.run_id} trialsData={trialsData?.trials} /> : null}
    </div>
  );
}

function taskProgressPct(events: RunEvent[], trials: Record<string, TrialLite[]>): number {
  // 运行中: 已收到 task_complete 的 task 数 / 已知 task 数 (从 trial_start 推断总数)
  const started = new Set<string>();
  const completed = new Set<string>();
  for (const e of events) {
    if (e.type === "task_start" && e.task_id) started.add(e.task_id);
    if (e.type === "task_complete" && e.task_id) completed.add(e.task_id);
  }
  const known = new Set([...Object.keys(trials), ...started]);
  if (known.size === 0) return 0;
  return Math.round((completed.size / known.size) * 100);
}

function LiveEventFeed({ events }: { events: RunEvent[] }) {
  if (events.length === 0) {
    return <div className="text-xs text-muted-foreground">等待事件…</div>;
  }
  const LABEL: Record<string, string> = {
    task_start: "任务开始",
    trial_start: "trial 开始",
    trial_complete: "trial 完成",
    task_complete: "任务完成",
    run_complete: "运行完成",
    error: "错误",
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>实时事件</CardTitle>
      </CardHeader>
      <CardContent className="mono max-h-40 overflow-y-auto text-xs">
        {events.slice(-12).map((e, i) => (
          <div key={i} className="py-0.5 text-muted-foreground">
            <span className="text-primary">{LABEL[e.type] ?? e.type}</span>
            {e.task_id ? ` ${e.task_id}` : ""}
            {e.trial_index != null ? ` #${e.trial_index}` : ""}
            {e.type === "trial_complete" ? (e.success ? " ✓" : " ✗") : ""}
            {e.type === "error" && e.error ? ` — ${String(e.error)}` : ""}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function TaskResultsTable({
  runId,
  taskIds,
  trials,
}: {
  runId: string;
  taskIds: string[];
  trials: Record<string, TrialLite[]>;
}) {
  if (taskIds.length === 0) {
    return <div className="text-sm text-muted-foreground">尚无任务数据</div>;
  }
  return (
    <Table>
      <thead>
        <tr>
          <Th>Task</Th>
          <Th>通过率</Th>
          <Th>平均分</Th>
          <Th>状态</Th>
          <Th>Trials</Th>
        </tr>
      </thead>
      <tbody>
        {taskIds.map((taskId) => {
          const taskTrials = trials[taskId] ?? [];
          const passed = taskTrials.filter((t) => t.success).length;
          const rate = taskTrials.length ? passed / taskTrials.length : 0;
          const avg = taskTrials.length
            ? taskTrials.reduce((acc, t) => acc + t.score, 0) / taskTrials.length
            : 0;
          const allDone = taskTrials.length > 0 && taskTrials.every((t) => t.success || t.error);
          return (
            <tr key={taskId}>
              <Td className="mono">{taskId}</Td>
              <Td>{fmtPct(rate)}</Td>
              <Td>{fmtScore(avg)}</Td>
              <Td>
                {!allDone ? (
                  <Badge tone="primary">运行中</Badge>
                ) : passed === taskTrials.length ? (
                  <Badge tone="success">通过</Badge>
                ) : (
                  <Badge tone="danger">失败 {passed}/{taskTrials.length}</Badge>
                )}
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  {taskTrials.map((t) => (
                    <Link
                      key={t.trial_index}
                      href={`/runs/${runId}/trials/${taskId}/${t.trial_index}`}
                      className="inline-flex"
                    >
                      <Badge tone={t.success ? "success" : "danger"}>
                        #{t.trial_index} {t.success ? "✓" : "✗"}
                      </Badge>
                    </Link>
                  ))}
                  {taskTrials.length === 0 ? (
                    <span className="text-xs text-muted-foreground">等待中…</span>
                  ) : null}
                </div>
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function FailedTrialsCard({
  runId,
  trialsData,
}: {
  runId: string;
  trialsData?: Record<string, TrialFull[]>;
}) {
  if (!trialsData) return null;
  const failed: Array<{ taskId: string; trial: TrialFull }> = [];
  for (const [taskId, trials] of Object.entries(trialsData)) {
    for (const t of trials) {
      if (!t.success) failed.push({ taskId, trial: t });
    }
  }
  if (failed.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>失败 Trial 明细 ({failed.length})</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {failed.map(({ taskId, trial }) => {
          const failedGraders = trial.grader_results.filter((g) => !g.passed);
          return (
            <div key={`${taskId}-${trial.trial_index}`} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between">
                <Link
                  className="mono text-sm text-primary hover:underline"
                  href={`/runs/${runId}/trials/${taskId}/${trial.trial_index}`}
                >
                  {taskId} #{trial.trial_index}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {fmtDuration(trial.duration_ms)}
                </span>
              </div>
              {trial.error ? (
                <div className="mt-1 text-xs text-danger">error: {trial.error}</div>
              ) : null}
              {failedGraders.length > 0 ? (
                <ul className="mt-1 flex flex-col gap-1">
                  {failedGraders.map((g) => (
                    <li key={g.grader_name} className="text-xs text-muted-foreground">
                      <span className="text-danger">{g.grader_name}</span> ({fmtScore(g.score)}):{" "}
                      {g.explanation}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
