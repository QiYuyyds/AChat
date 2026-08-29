"use client";

import { CoverageBars } from "@/components/dataset/coverage-bars";
import { ItemEditorDialog, type ItemDialogState } from "@/components/dataset/item-editor-dialog";
import { QualityReportPanel } from "@/components/dataset/quality-report-panel";
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
} from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import {
  useAddDatasetItem,
  useBumpDatasetVersion,
  useDataset,
  useDatasetCoverage,
  useDeleteDatasetItem,
  useQualityCheck,
  useRegressionExtract,
  useToSuite,
  useUpdateDatasetItem,
} from "@/lib/queries";
import type { DatasetItem, QualityReport, RegressionExtractResponse } from "@/lib/types";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

const BUMP_LABELS: Record<string, string> = {
  major: "major (不兼容变更)",
  minor: "minor (新增条目)",
  patch: "patch (修订)",
};

export default function DatasetDetailPage() {
  const params = useParams<{ ref: string }>();
  const ref = decodeURIComponent(params.ref);
  const { data: dataset, isLoading, error } = useDataset(ref);
  const { data: coverage } = useDatasetCoverage(ref);

  // 条目增删改
  const addItem = useAddDatasetItem(ref);
  const updateItem = useUpdateDatasetItem(ref);
  const deleteItem = useDeleteDatasetItem(ref);
  const [itemDialog, setItemDialog] = useState<ItemDialogState>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // 质量检查
  const qualityCheck = useQualityCheck(ref);
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);

  // to-suite
  const toSuite = useToSuite(ref);
  const [suiteName, setSuiteName] = useState("");
  const [toSuiteDone, setToSuiteDone] = useState<{ name: string; tasks: number } | null>(null);
  const [toSuiteError, setToSuiteError] = useState<string | null>(null);

  // 升版
  const bumpVersion = useBumpDatasetVersion(ref);
  const [bumpOpen, setBumpOpen] = useState(false);
  const [bumpType, setBumpType] = useState<"major" | "minor" | "patch">("minor");
  const [bumpNote, setBumpNote] = useState("");
  const [bumpError, setBumpError] = useState<string | null>(null);

  // 回归提取
  const regressionExtract = useRegressionExtract(ref);
  const [regOpen, setRegOpen] = useState(false);
  const [regRunId, setRegRunId] = useState("");
  const [regMaxItems, setRegMaxItems] = useState(50);
  const [regBump, setRegBump] = useState<"minor" | "major" | "patch" | "none">("minor");
  const [regResult, setRegResult] = useState<RegressionExtractResponse | null>(null);
  const [regError, setRegError] = useState<string | null>(null);

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error || !dataset) {
    return <div className="text-sm text-danger">数据集不存在或后端未连接 ({String(error)})</div>;
  }

  async function submitItem(item: DatasetItem, itemId: string | null) {
    if (itemId) {
      await updateItem.mutateAsync({ itemId, item });
    } else {
      await addItem.mutateAsync(item);
    }
  }

  async function runToSuite() {
    setToSuiteError(null);
    setToSuiteDone(null);
    try {
      const result = await toSuite.mutateAsync(suiteName.trim() || undefined);
      setToSuiteDone({ name: result.suite_name, tasks: result.task_count });
    } catch (e) {
      setToSuiteError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function runBump() {
    setBumpError(null);
    try {
      await bumpVersion.mutateAsync({ change_type: bumpType, note: bumpNote });
      setBumpOpen(false);
      setBumpNote("");
    } catch (e) {
      setBumpError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function runRegression() {
    setRegError(null);
    setRegResult(null);
    try {
      const result = await regressionExtract.mutateAsync({
        run_id: regRunId.trim(),
        max_items: regMaxItems,
        bump_version: regBump === "none" ? null : regBump,
      });
      setRegResult(result);
    } catch (e) {
      setRegError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 头部 */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mono text-xl font-semibold">{dataset.name}</h1>
          <p className="text-sm text-muted-foreground">
            {dataset.description || "—"} · v{dataset.version} · {dataset.items.length} 条
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {dataset.tags.map((t) => (
              <Badge key={t} tone="muted">#{t}</Badge>
            ))}
          </div>
        </div>
        <Link href={`/datasets/${dataset.id}/mine`}>
          <Button>⛏ 挖掘 / 生成</Button>
        </Link>
      </div>

      {toSuiteDone ? (
        <div className="flex items-center gap-3 rounded-lg border border-success/40 bg-success/10 p-3 text-sm">
          <span>
            已转换为 Suite <span className="mono font-semibold">{toSuiteDone.name}</span> (
            {toSuiteDone.tasks} 个任务)
          </span>
          <Link
            href={`/suites/${encodeURIComponent(toSuiteDone.name)}`}
            className="text-primary hover:underline"
          >
            查看 Suite 详情 →
          </Link>
        </div>
      ) : null}

      {/* 元信息 + 覆盖度 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>元信息</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">ID</span>
              <span className="mono">{dataset.id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">版本</span>
              <span className="mono">v{dataset.version}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">条目数</span>
              <span>{dataset.items.length}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">更新时间</span>
              <span className="text-muted-foreground">{fmtTime(dataset.updated_at)}</span>
            </div>
            {dataset.change_log.length > 0 ? (
              <div className="mt-2 border-t border-border/60 pt-2">
                <div className="mb-1 text-xs font-semibold text-muted-foreground">变更记录</div>
                <ul className="flex flex-col gap-1">
                  {[...dataset.change_log].reverse().map((c, i) => (
                    <li key={i} className="text-xs text-muted-foreground">
                      <span className="mono">v{c.version}</span>{" "}
                      <Badge tone="muted">{c.change_type}</Badge>{" "}
                      {c.note || "—"}{" "}
                      <span className="text-xs opacity-70">{fmtTime(c.at)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>
        <CoverageBars report={coverage ?? null} />
      </div>

      {/* 操作区 */}
      <Card>
        <CardHeader>
          <CardTitle>操作</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  qualityCheck.mutate(undefined, {
                    onSuccess: (report) => setQualityReport(report),
                  })
                }
                disabled={qualityCheck.isPending}
              >
                {qualityCheck.isPending ? "检查中…" : "质量检查"}
              </Button>
              <Input
                className="w-56"
                placeholder="Suite 名称 (缺省用数据集名)"
                value={suiteName}
                onChange={(e) => setSuiteName(e.target.value)}
              />
              <Button onClick={runToSuite} disabled={toSuite.isPending}>
                {toSuite.isPending ? "转换中…" : "转为 Suite"}
              </Button>
              <Button variant="outline" onClick={() => setBumpOpen(true)}>
                升版
              </Button>
              <Button variant="outline" onClick={() => setRegOpen(true)}>
                回归提取
              </Button>
            </div>
            {toSuiteError ? (
              <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                {toSuiteError}
              </pre>
            ) : null}
            {qualityReport ? <QualityReportPanel report={qualityReport} /> : null}
          </div>
        </CardContent>
      </Card>

      {/* 条目列表 */}
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>条目 ({dataset.items.length})</CardTitle>
          <Button className="h-8" onClick={() => setItemDialog({ mode: "add" })}>
            + 新增条目
          </Button>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>ID</Th>
                <Th>Prompt</Th>
                <Th>描述</Th>
                <Th>来源</Th>
                <Th>溯源</Th>
                <Th>Graders</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {dataset.items.map((item) => (
                <tr key={item.id} className="hover:bg-muted/40">
                  <Td className="mono">{item.id}</Td>
                  <Td className="max-w-64">
                    <div className="line-clamp-2">{item.prompt || "—"}</div>
                  </Td>
                  <Td className="max-w-40">
                    <div className="line-clamp-1 text-muted-foreground">
                      {item.description || "—"}
                    </div>
                  </Td>
                  <Td>
                    <SourceBadge type={item.source_type} />
                  </Td>
                  <Td className="mono max-w-40">
                    <div className="line-clamp-1 text-xs text-muted-foreground">
                      {item.source_ref || "—"}
                    </div>
                  </Td>
                  <Td>{item.graders.length}</Td>
                  <Td>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => setItemDialog({ mode: "edit", item })}
                      >
                        编辑
                      </Button>
                      {deletingId === item.id ? (
                        <>
                          <Button
                            variant="destructive"
                            className="h-7 px-2 text-xs"
                            disabled={deleteItem.isPending}
                            onClick={async () => {
                              await deleteItem.mutateAsync(item.id);
                              setDeletingId(null);
                            }}
                          >
                            确认
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-7 px-2 text-xs"
                            onClick={() => setDeletingId(null)}
                          >
                            取消
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          className="h-7 px-2 text-xs text-danger"
                          onClick={() => setDeletingId(item.id)}
                        >
                          删除
                        </Button>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
              {dataset.items.length === 0 ? (
                <tr>
                  <Td colSpan={7} className="text-sm text-muted-foreground">
                    还没有条目 — 手动新增、导入或使用「挖掘 / 生成」向导
                  </Td>
                </tr>
              ) : null}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      {/* 对话框 */}
      {itemDialog ? (
        <ItemEditorDialog
          state={itemDialog}
          onClose={() => setItemDialog(null)}
          onSubmit={submitItem}
        />
      ) : null}

      {bumpOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex w-full max-w-md flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-lg">
            <div className="text-sm font-semibold">语义化升版</div>
            <div className="flex gap-1">
              {(Object.keys(BUMP_LABELS) as Array<"major" | "minor" | "patch">).map((t) => (
                <button
                  key={t}
                  onClick={() => setBumpType(t)}
                  className={
                    bumpType === t
                      ? "rounded-lg bg-primary/15 px-3 py-1.5 text-xs font-medium text-primary"
                      : "rounded-lg px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
                  }
                >
                  {BUMP_LABELS[t]}
                </button>
              ))}
            </div>
            <div className="flex flex-col gap-1">
              <Label>变更说明</Label>
              <Input
                value={bumpNote}
                onChange={(e) => setBumpNote(e.target.value)}
                placeholder="如: 合入 trace 挖掘条目"
              />
            </div>
            {bumpError ? (
              <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                {bumpError}
              </pre>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setBumpOpen(false)}>
                取消
              </Button>
              <Button onClick={runBump} disabled={bumpVersion.isPending}>
                {bumpVersion.isPending ? "升版中…" : "升版"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {regOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex max-h-[85vh] w-full max-w-lg flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-lg">
            <div className="text-sm font-semibold">从 Run 提取回归样本</div>
            <p className="text-xs text-muted-foreground">
              将指定 run 中失败 trial 的输入提取为 regression 条目并入数据集 (质量闭环)。
            </p>
            <div className="flex flex-col gap-1">
              <Label>Run ID *</Label>
              <Input
                className="mono"
                value={regRunId}
                onChange={(e) => setRegRunId(e.target.value)}
                placeholder="run_xxx"
              />
            </div>
            <div className="flex gap-3">
              <div className="flex flex-1 flex-col gap-1">
                <Label>提取上限</Label>
                <Input
                  type="number"
                  min={1}
                  max={500}
                  value={regMaxItems}
                  onChange={(e) => setRegMaxItems(Number(e.target.value) || 50)}
                />
              </div>
              <div className="flex flex-1 flex-col gap-1">
                <Label>合入后升版</Label>
                <select
                  className="h-9 rounded-lg border border-border bg-background px-2 text-sm"
                  value={regBump}
                  onChange={(e) => setRegBump(e.target.value as typeof regBump)}
                >
                  <option value="none">不升版</option>
                  <option value="minor">minor</option>
                  <option value="major">major</option>
                  <option value="patch">patch</option>
                </select>
              </div>
            </div>
            {regError ? (
              <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
                {regError}
              </pre>
            ) : null}
            {regResult ? (
              <div className="rounded-lg border border-success/40 bg-success/10 p-3 text-xs">
                <div>
                  提取 {regResult.extraction.extracted} 条 · 合入{" "}
                  <span className="font-semibold">{regResult.merge.merged}</span> 条 · 当前版本{" "}
                  <span className="mono">v{regResult.version}</span>
                  {regResult.bumped ? " (已升版)" : ""}
                </div>
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRegOpen(false)}>
                关闭
              </Button>
              <Button
                onClick={runRegression}
                disabled={regressionExtract.isPending || !regRunId.trim()}
              >
                {regressionExtract.isPending ? "提取中…" : "提取并合入"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
