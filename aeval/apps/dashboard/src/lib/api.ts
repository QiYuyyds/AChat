/** REST 封装 — 统一走设置页配置的 apiBase (空 = 同源经 Next 代理)。 */

import { getApiBase } from "@/store/settings";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }

  /** FastAPI 422 校验错误 → 可读文本 (YAML 导入错误回显用) */
  formatDetail(): string {
    if (!Array.isArray(this.detail)) return "";
    return this.detail
      .map((d: { loc?: (string | number)[]; msg?: string }) => {
        const loc = (d.loc ?? []).slice(1).join(".") || "(body)";
        return `${loc}: ${d.msg ?? "invalid"}`;
      })
      .join("\n");
  }
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit & { json?: unknown }
): Promise<T> {
  const { json, ...rest } = init ?? {};
  const url = `${getApiBase()}${path}`;
  let resp: Response;
  try {
    resp = await fetch(url, {
      ...rest,
      headers: {
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(rest.headers ?? {}),
      },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch (e) {
    throw new ApiError(0, `无法连接后端 (${url}): ${String(e)}`);
  }
  if (!resp.ok) {
    let detail: unknown = null;
    let message = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body?.detail ?? body;
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, message, detail);
  }
  return (await resp.json()) as T;
}

export function eventSourceUrl(path: string): string {
  return `${getApiBase()}${path}`;
}
