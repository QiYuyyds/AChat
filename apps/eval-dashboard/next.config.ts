import type { NextConfig } from "next";

// Aeval Dashboard — 独立 Next.js 应用 (与 AChat 前端解耦, 见设计文档 §17.10/D4)。
// 开发期经 rewrites 将 /api/eval/* 代理到 AChat 后端 (免 CORS; SSE 经 Node 代理,
// 若观察到缓冲不实时, 在设置页直连后端地址即可, CORS 已由后端白名单覆盖)。
const BACKEND_URL = process.env.EVAL_BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/eval/:path*", destination: `${BACKEND_URL}/api/eval/:path*` },
    ];
  },
};

export default nextConfig;
