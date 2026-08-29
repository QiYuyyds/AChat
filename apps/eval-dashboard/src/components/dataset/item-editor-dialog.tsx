"use client";

import { Button, Label, Textarea } from "@/components/ui/primitives";
import { ApiError } from "@/lib/api";
import type { DatasetItem } from "@/lib/types";
import { useState } from "react";

const ITEM_TEMPLATE = {
  id: "i1",
  prompt: "给 Agent 的输入",
  description: "",
  graders: [{ type: "model", name: "model_based", config: { rubric: "回答是否正确" } }],
  env: {},
  metadata: { capabilities: ["qa"] },
};

export type ItemDialogState =
  | { mode: "add" }
  | { mode: "edit"; item: DatasetItem }
  | null;

/**
 * 条目新增/编辑对话框 — JSON textarea + 双层校验回显
 * (客户端 JSON 语法检查 + 服务端 422 字段级错误)
 */
export function ItemEditorDialog({
  state,
  onClose,
  onSubmit,
}: {
  state: ItemDialogState;
  onClose: () => void;
  onSubmit: (item: DatasetItem, itemId: string | null) => Promise<void>;
}) {
  const initial =
    state?.mode === "edit"
      ? JSON.stringify(state.item, null, 2)
      : JSON.stringify(ITEM_TEMPLATE, null, 2);
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (!state) return null;
  const editing = state.mode === "edit";
  const editingId = editing ? state.item.id : null;

  async function submit() {
    setError(null);
    let parsed: DatasetItem;
    try {
      parsed = JSON.parse(text) as DatasetItem;
    } catch (e) {
      setError(`JSON 语法错误: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setPending(true);
    try {
      await onSubmit(parsed, editingId);
      onClose();
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = e.formatDetail();
        setError(detail ? `服务端校验失败:\n${detail}` : e.message);
      } else {
        setError(String(e));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-lg">
        <div className="text-sm font-semibold">
          {editing ? `编辑条目: ${state.item.id}` : "新增条目"}
        </div>
        <Label>
          条目 JSON (服务端校验：缺 prompt / graders 非法等错误会逐字段回显)
        </Label>
        <Textarea
          className="mono min-h-80"
          rows={14}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {error ? (
          <pre className="mono whitespace-pre-wrap rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs text-danger">
            {error}
          </pre>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={pending}>
            {pending ? "保存中…" : editing ? "保存" : "新增"}
          </Button>
        </div>
      </div>
    </div>
  );
}
