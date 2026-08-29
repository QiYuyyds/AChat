"use client";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import { useCreateDataset, useImportDataset } from "@/lib/queries";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import yaml from "js-yaml";

type Tab = "manual" | "import";

export default function NewDatasetPage() {
  const [tab, setTab] = useState<Tab>("manual");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">新建数据集</h1>
          <p className="text-sm text-muted-foreground">
            手动表单创建，或粘贴 YAML/JSON 导入既有定义
          </p>
        </div>
        <Link href="/datasets">
          <Button variant="ghost">← 返回列表</Button>
        </Link>
      </div>

      <div className="flex gap-2">
        {(
          [
            ["manual", "手动表单"],
            ["import", "YAML / JSON 导入"],
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

      {tab === "manual" ? <ManualForm /> : <ImportForm />}
    </div>
  );
}

function ManualForm() {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createDataset = useCreateDataset();
  const router = useRouter();

  async function submit() {
    setError(null);
    try {
      const created = await createDataset.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        tags: tags.split(/[,\s]+/).map((t) => t.trim()).filter(Boolean),
      });
      router.push(`/datasets/${created.id}`);
    } catch (e) {
      setError(readError(e));
    }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>手动创建</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <Label>名称 *</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="support-qa" />
        </div>
        <div className="flex flex-col gap-1">
          <Label>描述</Label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="客服场景问答数据集"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label>标签 (逗号分隔)</Label>
          <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="support, qa" />
        </div>
        <p className="text-xs text-muted-foreground">
          创建后到详情页添加条目，或使用「挖掘 / 生成」向导填充。
        </p>
        {error ? <ErrorBox error={error} /> : null}
        <div>
          <Button onClick={submit} disabled={createDataset.isPending || !name.trim()}>
            {createDataset.isPending ? "创建中…" : "创建"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ImportForm() {
  const [format, setFormat] = useState<"yaml" | "json">("yaml");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const importDataset = useImportDataset();
  const router = useRouter();

  async function submit() {
    setError(null);
    // 客户端语法检查先行; 服务端 schema 校验错误 (422) 逐条回显, 不创建数据集
    try {
      if (format === "yaml") {
        yaml.load(content);
      } else {
        JSON.parse(content);
      }
    } catch (e) {
      setError(`${format.toUpperCase()} 语法错误: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    try {
      const created = await importDataset.mutateAsync({ content, format });
      router.push(`/datasets/${created.id}`);
    } catch (e) {
      setError(readError(e));
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>导入数据集</CardTitle>
        <div className="flex gap-1">
          {(["yaml", "json"] as const).map((f) => (
            <Badge
              key={f}
              tone={format === f ? "primary" : "muted"}
              className="cursor-pointer"
              onClick={() => setFormat(f)}
            >
              {f.toUpperCase()}
            </Badge>
          ))}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Label>
          粘贴数据集定义 (提交后服务端校验；条目缺 prompt/graders 等错误会逐条回显，不会创建)
        </Label>
        <Textarea
          rows={16}
          spellCheck={false}
          placeholder={`name: my-dataset\ndescription: 客服数据集\ntags:\n  - support\nitems:\n  - id: i1\n    prompt: 退款政策是什么？\n    graders:\n      - type: model\n        name: model_based\n    metadata:\n      capabilities:\n        - qa`}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        {error ? <ErrorBox error={error} /> : null}
        <div className="flex gap-2">
          <Button onClick={submit} disabled={importDataset.isPending || !content.trim()}>
            {importDataset.isPending ? "导入中…" : "导入"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ErrorBox({ error }: { error: string }) {
  return (
    <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
      {error}
    </pre>
  );
}

/** 服务端错误 → 可读文本 (422 校验错误逐字段回显) */
function readError(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.formatDetail();
    return detail ? `服务端校验失败:\n${detail}` : e.message;
  }
  return String(e);
}
