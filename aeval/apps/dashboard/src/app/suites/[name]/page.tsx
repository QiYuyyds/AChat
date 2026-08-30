"use client";

import { RunStatusBadge } from "@/components/run-status-badge";
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
import { fmtDuration, fmtScore, fmtTime } from "@/lib/format";
import { useCreateRun, useRuns, useSuite } from "@/lib/queries";
import { useParams, useRouter } from "next/navigation";

export default function SuiteDetailPage() {
  const params = useParams<{ name: string }>();
  const name = decodeURIComponent(params.name);
  const { data: suite, isLoading, error } = useSuite(name);
  const { data: runsData } = useRuns(100);
  const createRun = useCreateRun();
  const router = useRouter();

  const suiteRuns = (runsData?.runs ?? []).filter((r) => r.suite_name === name);

  async function startRun() {
    const created = await createRun.mutateAsync(name);
    router.push(`/runs/${created.run_id}`);
  }

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error || !suite) {
    return (
      <div className="text-sm text-danger">
        Suite 不存在或后端未连接 ({String(error)})
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="mono text-xl font-semibold">{suite.name}</h1>
          <p className="text-sm text-muted-foreground">
            {suite.description || "—"} · v{suite.version}
          </p>
        </div>
        <Button onClick={startRun} disabled={createRun.isPending}>
          {createRun.isPending ? "启动中…" : "▶ 运行 Suite"}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>任务清单 ({suite.tasks.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Task ID</Th>
                <Th>描述</Th>
                <Th>Trials</Th>
                <Th>Graders</Th>
              </tr>
            </thead>
            <tbody>
              {suite.tasks.map((t) => (
                <tr key={t.id}>
                  <Td className="mono">{t.id}</Td>
                  <Td>{t.description || "—"}</Td>
                  <Td>{t.max_trials}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {t.graders.map((g) => (
                        <Badge key={g.name} tone="muted">
                          {g.name}
                        </Badge>
                      ))}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>运行历史 ({suiteRuns.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {suiteRuns.length === 0 ? (
            <div className="text-sm text-muted-foreground">还没有运行记录</div>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>状态</Th>
                  <Th>pass@1</Th>
                  <Th>平均分</Th>
                  <Th>开始时间</Th>
                  <Th>耗时</Th>
                </tr>
              </thead>
              <tbody>
                {suiteRuns.map((r) => (
                  <tr key={r.run_id} className="hover:bg-muted/40">
                    <Td className="mono">
                      <a
                        className="text-primary hover:underline"
                        href={`/runs/${r.run_id}`}
                      >
                        {r.run_id}
                      </a>
                    </Td>
                    <Td>
                      <RunStatusBadge status={r.status} />
                    </Td>
                    <Td>{fmtScore(r.summary?.pass_at_k?.["1"])}</Td>
                    <Td>{fmtScore(r.summary?.avg_score)}</Td>
                    <Td className="text-muted-foreground">{fmtTime(r.started_at)}</Td>
                    <Td>{fmtDuration(r.duration_ms)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
