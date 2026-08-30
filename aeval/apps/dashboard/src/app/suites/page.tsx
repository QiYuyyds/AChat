"use client";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Label, Textarea } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import { useCreateSuite, useSuites } from "@/lib/queries";
import type { SuiteDetail, SuiteListItem } from "@/lib/types";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import yaml from "js-yaml";

export default function SuitesPage() {
  const { data, isLoading, error } = useSuites();
  const [showImport, setShowImport] = useState(false);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Suites</h1>
          <p className="text-sm text-muted-foreground">评测套件列表</p>
        </div>
        <Button onClick={() => setShowImport((v) => !v)}>
          {showImport ? "收起导入" : "YAML 导入"}
        </Button>
      </div>

      {showImport ? <YamlImportPanel onDone={() => setShowImport(false)} /> : null}

      {isLoading ? <div className="text-sm text-muted-foreground">加载中…</div> : null}
      {error ? <div className="text-sm text-danger">加载失败: {String(error)}</div> : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {(data?.suites ?? []).map((s: SuiteListItem) => (
          <Link key={s.name} href={`/suites/${encodeURIComponent(s.name)}`}>
            <Card className="h-full transition hover:border-primary/60">
              <CardHeader>
                <CardTitle className="mono">{s.name}</CardTitle>
              </CardHeader>
              <CardContent className="flex items-center justify-between text-sm text-muted-foreground">
                <span className="line-clamp-1">{s.description || "—"}</span>
                <Badge tone="primary">{s.task_count} tasks</Badge>
              </CardContent>
            </Card>
          </Link>
        ))}
        {!isLoading && (data?.suites ?? []).length === 0 ? (
          <div className="text-sm text-muted-foreground">
            还没有 suite — 点击右上角「YAML 导入」创建第一个
          </div>
        ) : null}
      </div>
    </div>
  );
}

function YamlImportPanel({ onDone }: { onDone: () => void }) {
  const [yamlText, setYamlText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createSuite = useCreateSuite();
  const router = useRouter();

  async function submit() {
    setError(null);
    // 1. 客户端 YAML 语法检查 (js-yaml); 2. 服务端 schema 校验错误 → 422 回显
    let parsed: unknown;
    try {
      parsed = yaml.load(yamlText);
    } catch (e) {
      setError(`YAML 语法错误: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    try {
      const created = await createSuite.mutateAsync(parsed as SuiteDetail);
      onDone();
      router.push(`/suites/${encodeURIComponent(created.name)}`);
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = e.formatDetail();
        setError(detail ? `服务端校验失败:\n${detail}` : e.message);
      } else {
        setError(String(e));
      }
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>以 YAML 导入 Suite</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Label>
          粘贴 suite YAML (提交后服务端校验;错误逐字段回显,不会创建 suite)
        </Label>
        <Textarea
          className="mono"
          rows={14}
          spellCheck={false}
          placeholder={`name: my-suite\nversion: 1.0.0\ntasks:\n  - id: t1\n    prompt: ...\n    graders:\n      - type: code\n        name: code_based`}
          value={yamlText}
          onChange={(e) => setYamlText(e.target.value)}
        />
        {error ? (
          <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
            {error}
          </pre>
        ) : null}
        <div className="flex gap-2">
          <Button onClick={submit} disabled={createSuite.isPending || !yamlText.trim()}>
            {createSuite.isPending ? "导入中…" : "导入"}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            取消
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
