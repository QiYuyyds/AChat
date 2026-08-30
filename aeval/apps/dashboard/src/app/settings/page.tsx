"use client";

import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "@/components/ui/primitives";
import { apiFetch, ApiError } from "@/lib/api";
import { useEvalSettings } from "@/store/settings";
import { useState } from "react";

export default function SettingsPage() {
  const { apiBase, phoenixUiUrl, setApiBase, setPhoenixUiUrl } = useEvalSettings();
  const [apiDraft, setApiDraft] = useState(apiBase);
  const [phoenixDraft, setPhoenixDraft] = useState(phoenixUiUrl);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  async function testConnection() {
    setTesting(true);
    setResult(null);
    // 连接测试针对「当前草稿地址」而不是已保存值 — 测完生效再保存
    const prev = useEvalSettings.getState().apiBase;
    useEvalSettings.setState({ apiBase: apiDraft });
    try {
      const health = await apiFetch<{ status: string; runner_configured: boolean }>(
        "/api/eval/health"
      );
      setResult({
        ok: true,
        message: `连接成功:后端 ${health.status},runner ${
          health.runner_configured ? "已装配" : "未装配 (POST /runs 将返回 503,检查 EVAL_AGENT_ID)"
        }`,
      });
      setApiBase(apiDraft);
      setPhoenixUiUrl(phoenixDraft);
    } catch (e) {
      const message =
        e instanceof ApiError
          ? `连接失败: ${e.message}`
          : `连接失败: ${String(e)}`;
      setResult({ ok: false, message });
    } finally {
      useEvalSettings.setState({ apiBase: prev });
      setTesting(false);
    }
  }

  return (
    <div className="flex max-w-xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          连接配置 (保存在浏览器本地,刷新后保留)
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>后端 API 地址</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Label htmlFor="api-base">
            留空 = 同源(经 Next 代理);直连填后端地址,如 http://localhost:8000
          </Label>
          <Input
            id="api-base"
            className="mono"
            placeholder="http://localhost:8000"
            value={apiDraft}
            onChange={(e) => setApiDraft(e.target.value)}
          />
          <Label htmlFor="phoenix-url">Phoenix UI 地址 (Trial 外链跳转用)</Label>
          <Input
            id="phoenix-url"
            className="mono"
            placeholder="http://localhost:6006"
            value={phoenixDraft}
            onChange={(e) => setPhoenixDraft(e.target.value)}
          />
          <div className="flex items-center gap-3">
            <Button onClick={testConnection} disabled={testing}>
              {testing ? "测试中…" : "测试并保存连接"}
            </Button>
          </div>
          {result ? (
            <div className={result.ok ? "text-sm text-success" : "text-sm text-danger"}>
              {result.message}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
