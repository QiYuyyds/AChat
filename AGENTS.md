<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version (16.2.6) has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

---

# AChat — Agent 协作入口

任何 AI 协作工具进入本项目工作时，请：

1. **先读 [`CLAUDE.md`](./CLAUDE.md)** —— 项目级 AI 协作约定（技术栈、代码风格、安全约束、协作规则）
2. **再读 [`openspec/project.md`](./openspec/project.md) 与 [`openspec/specs/`](./openspec/specs/)** —— OpenSpec 能力契约
3. **需要细节时读 [`specs/`](./specs/)** —— 编号版详细规格文档
4. **不确定时停下来问**（详见 CLAUDE.md §6.2）

本文件保留供 Codex / OpenCode 等期望读取 `AGENTS.md` 的工具使用。所有项目级协作规则以 `CLAUDE.md` 为单一信息源，本文件不重复其内容。
