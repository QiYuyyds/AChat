"use client";

/** 连接设置 (Settings 页可改): 后端 API 地址 + Phoenix endpoint。
 *
 * apiBase 为空字符串 → 同源请求, 经 Next rewrites 代理到后端 (开发期默认);
 * 直连模式 (如代理缓冲影响 SSE 实时性) 在设置页填入后端绝对地址。
 * 持久化于 localStorage, 刷新后保留。
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface EvalSettings {
  apiBase: string;
  phoenixUiUrl: string;
  setApiBase: (v: string) => void;
  setPhoenixUiUrl: (v: string) => void;
}

export const DEFAULT_PHOENIX_URL = "http://localhost:6006";

export const useEvalSettings = create<EvalSettings>()(
  persist(
    (set) => ({
      apiBase: "",
      phoenixUiUrl: DEFAULT_PHOENIX_URL,
      setApiBase: (apiBase) => set({ apiBase }),
      setPhoenixUiUrl: (phoenixUiUrl) => set({ phoenixUiUrl }),
    }),
    { name: "aeval-dashboard-settings" }
  )
);

export function getApiBase(): string {
  return useEvalSettings.getState().apiBase.replace(/\/$/, "");
}

export function phoenixTraceUrl(
  phoenixBase: string,
  traceId: string,
  projectName = "default"
): string {
  const base = phoenixBase.replace(/\/$/, "");
  return `${base}/projects/${projectName}/traces/${traceId}`;
}
