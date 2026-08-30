"use client";

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
import { fmtDuration, fmtScore } from "@/lib/format";
import { useRunTrials } from "@/lib/queries";
import type { TrialFull } from "@/lib/types";
import { phoenixTraceUrl, useEvalSettings } from "@/store/settings";
import { useParams } from "next/navigation";
import { useState } from "react";

export default function TrialDetailPage() {
  const params = useParams<{ runId: string; taskId: string; trialIndex: string }>();
  const runId = params.runId;
  const taskId = params.taskId;
  const trialIndex = Number(params.trialIndex);
  const { data, isLoading, error } = useRunTrials(runId);
  const phoenixBase = useEvalSettings((s) => s.phoenixUiUrl);

  const trial = data?.trials?.[taskId]?.find((t) => t.trial_index === trialIndex);
  const avgScore = trial?.grader_results?.length
    ? trial.grader_results.reduce((acc, g) => acc + g.score, 0) / trial.grader_results.length
    : 0;

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error) return <div className="text-sm text-danger">加载失败: {String(error)}</div>;
  if (!trial) {
    return (
      <div className="text-sm text-danger">
        Trial 不存在: {taskId} #{trialIndex}
      </div>
    );
  }

  const traceUrl = trial.trace_id
    ? phoenixTraceUrl(phoenixBase, trial.trace_id)
    : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mono text-xl font-semibold">
            {taskId} <span className="text-muted-foreground">#{trialIndex}</span>
          </h1>
          <p className="text-sm text-muted-foreground">
            run <span className="mono">{runId}</span> · 耗时 {fmtDuration(trial.duration_ms)} ·
            score {fmtScore(avgScore)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={trial.success ? "success" : "danger"}>
            {trial.success ? "通过" : "失败"}
          </Badge>
          {traceUrl ? (
            <a
              className="rounded-lg border border-border px-3 py-1.5 text-sm text-primary hover:bg-muted"
              href={traceUrl}
              target="_blank"
              rel="noreferrer"
            >
              Phoenix Trace ↗
            </a>
          ) : (
            <span className="text-xs text-muted-foreground">
              {trial.trace_id ? "" : "无 trace_id (tracing 未开启或 trial 失败)"}
            </span>
          )}
        </div>
      </div>

      {trial.error ? (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          error: {trial.error}
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Grader 评分分解</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Grader</Th>
                <Th>类型</Th>
                <Th>分数</Th>
                <Th>通过</Th>
                <Th>说明</Th>
              </tr>
            </thead>
            <tbody>
              {trial.grader_results.map((g) => (
                <tr key={g.grader_name}>
                  <Td className="mono">{g.grader_name}</Td>
                  <Td>{g.grader_type}</Td>
                  <Td>{fmtScore(g.score)}</Td>
                  <Td>
                    <Badge tone={g.passed ? "success" : "danger"}>
                      {g.passed ? "✓" : "✗"}
                    </Badge>
                  </Td>
                  <Td className="text-muted-foreground">{g.explanation}</Td>
                </tr>
              ))}
              {trial.grader_results.length === 0 ? (
                <tr>
                  <Td colSpan={5} className="text-muted-foreground">
                    无评分结果
                  </Td>
                </tr>
              ) : null}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <TranscriptCard transcript={trial.transcript} />
      <OutcomeCard outcome={trial.outcome} />
    </div>
  );
}

function TranscriptCard({ transcript }: { transcript: TrialFull["transcript"] }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Transcript ({transcript.length} 条消息)
          <button
            className="ml-3 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "收起" : "展开"}
          </button>
        </CardTitle>
      </CardHeader>
      {expanded ? (
        <CardContent className="flex flex-col gap-3">
          {transcript.length === 0 ? (
            <div className="text-sm text-muted-foreground">空 transcript</div>
          ) : null}
          {transcript.map((m, i) => (
            <div key={m.id ?? i} className="rounded-lg border border-border p-3">
              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                <Badge tone={m.role === "user" ? "primary" : "muted"}>{m.role}</Badge>
                {m.agent_id ? <span className="mono">{m.agent_id}</span> : null}
                {m.run_id ? <span className="mono">run {m.run_id}</span> : null}
                <span>{m.status}</span>
              </div>
              <pre className="mono whitespace-pre-wrap text-xs">{m.content || "(无文本)"}</pre>
            </div>
          ))}
        </CardContent>
      ) : null}
    </Card>
  );
}

function OutcomeCard({ outcome }: { outcome: TrialFull["outcome"] }) {
  const files = Object.entries(outcome.files ?? {});
  const artifacts = outcome.artifacts ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Outcome
          <span className="ml-2 text-xs text-muted-foreground">
            conversation {String(outcome.conversation_id ?? "—")}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {outcome.trace_id_unavailable ? (
          <div className="text-xs text-warning">{String(outcome.trace_id_unavailable)}</div>
        ) : null}

        <div>
          <div className="mb-2 text-sm font-medium">Workspace 文件 ({files.length})</div>
          {files.length === 0 ? (
            <div className="text-sm text-muted-foreground">无文件</div>
          ) : (
            <div className="flex flex-col gap-2">
              {files.map(([path, content]) => (
                <details key={path} className="rounded-lg border border-border p-2">
                  <summary className="mono cursor-pointer text-xs">{path}</summary>
                  <pre className="mono mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs">
                    {content}
                  </pre>
                </details>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="mb-2 text-sm font-medium">Artifacts ({artifacts.length})</div>
          {artifacts.length === 0 ? (
            <div className="text-sm text-muted-foreground">无产物</div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {artifacts.map((a, i) => (
                <Badge key={(a["id"] as string) ?? i} tone="primary">
                  {String(a["title"] ?? a["id"] ?? "?")} ({String(a["type"] ?? "?")})
                </Badge>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
