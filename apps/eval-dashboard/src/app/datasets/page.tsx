"use client";

import { SourceBadge } from "@/components/dataset/source-badge";
import { Badge, Button, Card, Input, Table, Td, Th } from "@/components/ui/primitives";
import { fmtTime } from "@/lib/format";
import { useDatasets, useDeleteDataset } from "@/lib/queries";
import type { DatasetSummary } from "@/lib/types";
import Link from "next/link";
import { useMemo, useState } from "react";

export default function DatasetsPage() {
  const [tagFilter, setTagFilter] = useState("");
  const [appliedTag, setAppliedTag] = useState<string | null>(null);
  const { data, isLoading, error } = useDatasets(appliedTag);
  const deleteDataset = useDeleteDataset();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  // 标签筛选下拉候选 — 从当前返回列表聚合 (已过滤时仍可切换)
  const allTags = useMemo(() => {
    const set = new Set<string>();
    for (const d of data?.datasets ?? []) for (const t of d.tags) set.add(t);
    return [...set].sort();
  }, [data]);

  function applyTag(tag: string | null) {
    setAppliedTag(tag);
  }

  async function remove(ds: DatasetSummary) {
    await deleteDataset.mutateAsync(ds.id);
    setConfirmingId(null);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Datasets</h1>
          <p className="text-sm text-muted-foreground">
            评测数据集 — 构建、质检、挖掘与转 Suite
          </p>
        </div>
        <Link href="/datasets/new">
          <Button>+ 新建 / 导入</Button>
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-48"
          placeholder="按标签筛选, 回车确认"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") applyTag(tagFilter.trim() || null);
          }}
        />
        {allTags.map((tag) => (
          <button
            key={tag}
            onClick={() => applyTag(appliedTag === tag ? null : tag)}
            className={
              appliedTag === tag
                ? "rounded-md bg-primary/15 px-2 py-0.5 text-xs text-primary"
                : "rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground hover:bg-primary/10"
            }
          >
            #{tag}
          </button>
        ))}
        {appliedTag ? (
          <Button variant="ghost" onClick={() => { setTagFilter(""); applyTag(null); }}>
            清除筛选
          </Button>
        ) : null}
      </div>

      {isLoading ? <div className="text-sm text-muted-foreground">加载中…</div> : null}
      {error ? <div className="text-sm text-danger">加载失败: {String(error)}</div> : null}

      <Card>
        <Table>
          <thead>
            <tr>
              <Th>名称</Th>
              <Th>版本</Th>
              <Th>条目数</Th>
              <Th>标签</Th>
              <Th>更新时间</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {(data?.datasets ?? []).map((d) => (
              <tr key={d.id} className="hover:bg-muted/40">
                <Td>
                  <Link
                    href={`/datasets/${d.id}`}
                    className="mono text-primary hover:underline"
                  >
                    {d.name}
                  </Link>
                  {d.description ? (
                    <div className="text-xs text-muted-foreground line-clamp-1">
                      {d.description}
                    </div>
                  ) : null}
                </Td>
                <Td className="mono">v{d.version}</Td>
                <Td>{d.item_count}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {d.tags.map((t) => (
                      <Badge key={t} tone="muted">#{t}</Badge>
                    ))}
                  </div>
                </Td>
                <Td className="text-muted-foreground">{fmtTime(d.updated_at)}</Td>
                <Td>
                  {confirmingId === d.id ? (
                    <div className="flex gap-1">
                      <Button
                        variant="destructive"
                        className="h-7 px-2 text-xs"
                        disabled={deleteDataset.isPending}
                        onClick={() => remove(d)}
                      >
                        {deleteDataset.isPending ? "删除中…" : "确认删除"}
                      </Button>
                      <Button
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => setConfirmingId(null)}
                      >
                        取消
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      className="h-7 px-2 text-xs text-danger"
                      onClick={() => setConfirmingId(d.id)}
                    >
                      删除
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
            {!isLoading && (data?.datasets ?? []).length === 0 ? (
              <tr>
                <Td colSpan={6} className="text-sm text-muted-foreground">
                  还没有数据集 — 点击右上角「新建 / 导入」创建第一个
                </Td>
              </tr>
            ) : null}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
