"use client";

import { Badge, Card, Input, Table, Td, Th } from "@/components/ui/primitives";
import { useTasks } from "@/lib/queries";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

export default function TasksPage() {
  const { data, isLoading, error } = useTasks();
  const [search, setSearch] = useState("");
  const router = useRouter();

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const tasks = data?.tasks ?? [];
    if (!q) return tasks;
    return tasks.filter(
      (t) =>
        t.id.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q) ||
        t.suite_name.toLowerCase().includes(q),
    );
  }, [data, search]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Task 库</h1>
        <p className="text-sm text-muted-foreground">
          跨 Suite 的评测任务 — 共 {data?.total ?? 0} 个
        </p>
      </div>

      <Input
        className="max-w-md"
        placeholder="搜索 task ID / 描述 / suite 名称…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isLoading ? <div className="text-sm text-muted-foreground">加载中…</div> : null}
      {error ? <div className="text-sm text-danger">加载失败: {String(error)}</div> : null}

      <Card>
        <Table>
          <thead>
            <tr>
              <Th>Task ID</Th>
              <Th>描述</Th>
              <Th>Suite</Th>
              <Th>Graders</Th>
              <Th>Trials</Th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((t) => (
              <tr
                key={`${t.suite_name}:${t.id}`}
                className="cursor-pointer hover:bg-muted/40"
                onClick={() => router.push(`/tasks/${encodeURIComponent(t.id)}`)}
              >
                <Td className="mono text-primary">{t.id}</Td>
                <Td className="max-w-80">
                  <div className="line-clamp-1">{t.description || "—"}</div>
                </Td>
                <Td className="mono">{t.suite_name}</Td>
                <Td>
                  <Badge tone="muted">{t.grader_count}</Badge>
                </Td>
                <Td>{t.max_trials}</Td>
              </tr>
            ))}
            {!isLoading && filtered.length === 0 ? (
              <tr>
                <Td colSpan={5} className="text-sm text-muted-foreground">
                  没有匹配的任务
                </Td>
              </tr>
            ) : null}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
