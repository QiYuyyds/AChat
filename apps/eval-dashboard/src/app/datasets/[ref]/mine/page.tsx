"use client";

import { SourceBadge } from "@/components/dataset/source-badge";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Table,
  Td,
  Th,
  Textarea,
} from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import { useDataset, useFromLLM, useFromTrace } from "@/lib/queries";
import type { DatasetItem, FromLLMResponse, FromTraceResponse } from "@/lib/types";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

type Tab = "trace" | "llm";

const STRATEGY_LABELS: Record<string, string> = {
  failed_tasks: "失败任务 (failed_tasks)",
  long_running: "长耗时任务 (long_running)",
  diverse_sampling: "多样性采样 (diverse_sampling)",
};

export default function MineWizardPage() {
  const params = useParams<{ ref: string }>();
  const ref = decodeURIComponent(params.ref);
  const { data: dataset } = useDataset(ref);
  const [tab, setTab] = useState<Tab>("trace");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">挖掘 / 生成向导</h1>
          <p className="text-sm text-muted-foreground">
            数据集 <span className="mono">{dataset?.name ?? ref}</span> — 从 trace 挖掘或由 LLM 生成条目
          </p>
        </div>
        <Link href={`/datasets/${dataset?.id ?? ref}`}>
          <Button variant="ghost">← 返回详情</Button>
        </Link>
      </div>

      <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
        提交即直接合入数据集 (后端无预览端点)。如误合入，可到详情页的条目列表逐条删除；
        需要隔离时建议先复制一个新数据集试验。
      </div>

      <div className="flex gap-2">
        {(
          [
            ["trace", "Trace 挖掘"],
            ["llm", "LLM 生成"],
          ] as Array<[Tab, string]>
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={
              tab === key
                ? "rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary"
                : "rounded-lg px-4 py-2 text-sm text-muted-foreground hover:bg-muted"
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "trace" ? <TraceMiningForm ref_={ref} /> : <LLMGenerateForm ref_={ref} />}
    </div>
  );
}

/* ─── Trace Mining ─────────────────────────────────────────────────────────── */

function TraceMiningForm({ ref_ }: { ref_: string }) {
  const [strategy, setStrategy] = useState("failed_tasks");
  const [limit, setLimit] = useState(20);
  const [candidateLimit, setCandidateLimit] = useState(100);
  const fromTrace = useFromTrace(ref_);
  const [result, setResult] = useState<FromTraceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setResult(null);
    try {
      const r = await fromTrace.mutateAsync({
        strategy,
        limit,
        candidate_limit: candidateLimit,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>1 · 参数确认</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label>挖掘策略</Label>
            <select
              className="h-9 rounded-lg border border-border bg-background px-2 text-sm"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              {Object.entries(STRATEGY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              failed_tasks = 挖掘失败 trace；long_running = 长耗时 trace；diverse_sampling = 多样性采样。
              候选中无法还原 prompt 的 trace 会被 skipped (不猜测)。
            </p>
          </div>
          <div className="flex gap-3">
            <div className="flex flex-1 flex-col gap-1">
              <Label>最多产出条目数</Label>
              <Input
                type="number"
                min={1}
                max={200}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value) || 20)}
              />
            </div>
            <div className="flex flex-1 flex-col gap-1">
              <Label>最多检查候选 trace 数</Label>
              <Input
                type="number"
                min={1}
                max={1000}
                value={candidateLimit}
                onChange={(e) => setCandidateLimit(Number(e.target.value) || 100)}
              />
            </div>
          </div>
          <div>
            <Button onClick={submit} disabled={fromTrace.isPending}>
              {fromTrace.isPending ? "挖掘中…" : "开始挖掘并合入"}
            </Button>
          </div>
          {error ? (
            <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
              {error}
            </pre>
          ) : null}
        </CardContent>
      </Card>

      {result ? <TraceMiningResult result={result} datasetRef={ref_} /> : null}
    </div>
  );
}

function TraceMiningResult({
  result,
  datasetRef,
}: {
  result: FromTraceResponse;
  datasetRef: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>2 · 结果确认</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="success">新增 {result.merged} 条</Badge>
          <Badge tone="warning">skipped {result.mining.skipped_count} 条</Badge>
          <span className="text-xs text-muted-foreground">
            候选 {result.mining.candidates} · 实检 {result.mining.inspected} · 数据集现有{" "}
            {result.item_count} 条
          </span>
        </div>

        {result.merged > 0 ? (
          <div>
            <div className="mb-1 text-xs font-semibold text-muted-foreground">
              本次合入的条目 (来源 trace_mining, 溯源为 trace ID)
            </div>
            <MinedItemsTable ids={result.mining.item_ids} datasetRef={datasetRef} />
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">本次没有合入任何条目。</div>
        )}

        {result.mining.skipped.length > 0 ? (
          <div>
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Skipped 明细</div>
            <ul className="flex flex-col gap-1">
              {result.mining.skipped.map((s, i) => (
                <li key={i} className="rounded-md border border-border/60 px-2 py-1 text-xs">
                  <span className="mono">{s.trace_id ?? "—"}</span>
                  <span className="ml-2 text-muted-foreground">{s.reason ?? "unknown"}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <Link href={`/datasets/${result.dataset_id}`} className="text-sm text-primary hover:underline">
          返回数据集详情查看全部条目 →
        </Link>
      </CardContent>
    </Card>
  );
}

function MinedItemsTable({ ids, datasetRef }: { ids: string[]; datasetRef: string }) {
  const set = new Set(ids);
  const { data: dataset } = useDataset(datasetRef);
  const items = (dataset?.items ?? []).filter((i) => set.has(i.id));
  return (
    <Table>
      <thead>
        <tr>
          <Th>ID</Th>
          <Th>Prompt</Th>
          <Th>来源</Th>
        </tr>
      </thead>
      <tbody>
        {items.map((item: DatasetItem) => (
          <tr key={item.id}>
            <Td className="mono">{item.id}</Td>
            <Td className="max-w-72">
              <div className="line-clamp-2">{item.prompt}</div>
            </Td>
            <Td>
              <SourceBadge type={item.source_type} />
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

/* ─── LLM 生成 ─────────────────────────────────────────────────────────────── */

function LLMGenerateForm({ ref_ }: { ref_: string }) {
  const [scenario, setScenario] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const [count, setCount] = useState(5);
  const fromLLM = useFromLLM(ref_);
  const [result, setResult] = useState<FromLLMResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setResult(null);
    try {
      const r = await fromLLM.mutateAsync({
        scenario: scenario.trim(),
        capabilities: capabilities
          .split(/[,\s]+/)
          .map((c) => c.trim())
          .filter(Boolean),
        count,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>1 · 参数确认</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label>场景描述 *</Label>
            <Textarea
              className="min-h-24"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              placeholder="例：电商客服售后场景 — 退款、换货、物流查询等用户咨询"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label>能力维度 (逗号分隔)</Label>
            <Input
              value={capabilities}
              onChange={(e) => setCapabilities(e.target.value)}
              placeholder="qa, tool_use, escalation"
            />
          </div>
          <div className="flex flex-1 flex-col gap-1">
            <Label>生成数量</Label>
            <Input
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(Number(e.target.value) || 5)}
            />
          </div>
          <div>
            <Button onClick={submit} disabled={fromLLM.isPending || !scenario.trim()}>
              {fromLLM.isPending ? "生成中…" : "开始生成并合入"}
            </Button>
          </div>
          {error ? (
            <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
              {error}
            </pre>
          ) : null}
        </CardContent>
      </Card>

      {result ? (
        <Card>
          <CardHeader>
            <CardTitle>2 · 结果确认</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="success">生成 {result.generation.generated} 条</Badge>
              {result.generation.invalid_count > 0 ? (
                <Badge tone="warning">无效 {result.generation.invalid_count} 条 (未合入)</Badge>
              ) : null}
              <span className="text-xs text-muted-foreground">
                数据集现有 {result.item_count} 条
              </span>
            </div>
            {result.generation.item_ids.length > 0 ? (
              <div className="text-xs text-muted-foreground">
                合入条目:{" "}
                {result.generation.item_ids.map((id) => (
                  <span key={id} className="mono mr-1">
                    {id}
                  </span>
                ))}
              </div>
            ) : null}
            <Link href={`/datasets/${result.dataset_id}`} className="text-primary hover:underline">
              返回数据集详情查看全部条目 →
            </Link>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
