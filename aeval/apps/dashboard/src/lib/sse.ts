/** SSE 订阅工具 — 快照+增量协议的增量侧 (§17.3)。
 *
 * EventSource 自带自动重连;断线重连后由调用方先重拉快照再重新订阅
 * (本封装在 error 时回调 onReset 提示调用方失效快照查询)。
 * run_complete 收尾后服务端关流,EventSource 会自动重连 → 手动关闭。
 */

import { eventSourceUrl } from "@/lib/api";
import type { RunEvent } from "@/lib/types";

export interface SubscribeOptions {
  onEvent: (event: RunEvent) => void;
  /** 连接错误/断开 — 调用方应重拉快照 (快照+增量恢复协议) */
  onError?: () => void;
}

export function subscribeRunEvents(runId: string, opts: SubscribeOptions): () => void {
  const es = new EventSource(eventSourceUrl(`/api/eval/runs/${runId}/stream`));

  es.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as RunEvent;
      opts.onEvent(event);
      if (event.type === "run_complete") {
        // 终态已到: 服务端即将关流, 主动关闭防自动重连
        es.close();
      }
    } catch {
      // 非 JSON 帧忽略
    }
  };

  es.onerror = () => {
    // readyState DONE 表示服务端已正常关流 (run_complete 已消费) — 不算错误
    if (es.readyState === EventSource.CLOSED) return;
    opts.onError?.();
  };

  return () => es.close();
}
