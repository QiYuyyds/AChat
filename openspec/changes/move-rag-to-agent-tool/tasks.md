## 1. Backend: agent-builder tool list

- [x] 1.1 Add `"rag_search"` to `_AVAILABLE_AGENT_TOOLS` tuple in `backend/app/api/agents.py` (line ~498).
- [x] 1.2 Add `rag_search` entry to `_AGENT_TOOL_META` dict in `backend/app/api/agents.py` with label and desc.
- [x] 1.3 Add `"rag_search"` to the `researcher` preset's `tools` list in `_AGENT_TOOL_PRESETS` in `backend/app/api/agents.py`.

## 2. Backend: agent_runner — remove conversation-level RAG injection

- [x] 2.1 Delete the `conv.rag_enabled` → inject `RAG_TOOLS` block in `backend/app/services/agent_runner.py` (line 2053-2070). `rag_search` now flows naturally from `agent.tool_names` via baseline merge.
- [x] 2.2 Simplify the RAG prompt guidance in `agent_runner.py` (line 3769-3794): change `has_rag` check to only detect `"rag_search" in tools`; remove `rag_ingest` / `rag_list_documents` / `rag_delete_document` guidance lines; update usage suggestion to only mention retrieval (not ingest).

## 3. Backend: remove conversation rag-mode endpoint

- [x] 3.1 Delete the `PATCH /conversations/{conversation_id}/rag-mode` endpoint in `backend/app/api/conversations.py` (line 195-207).
- [x] 3.2 Remove `rag_enabled` field from request schemas in `backend/app/schemas/requests.py` (line 57 and line 101).

## 4. Backend: deprecate rag_enabled column

- [x] 4.1 In `backend/app/db/models.py`, add a comment on `Conversation.rag_enabled` (line 273) marking it deprecated — column retained for backward compat, no longer read or written.

## 5. Frontend: agent-builder config

- [x] 5.1 Add `'rag_search'` to `AVAILABLE_AGENT_TOOLS` array in `src/shared/agent-builder-config.ts` (line 42-48).
- [x] 5.2 Update `AgentToolName` type to include `'rag_search'`.
- [x] 5.3 Add `rag_search` metadata entry to `AGENT_TOOL_META` (label: '知识库检索', desc: '在知识库中检索相关文档片段，返回匹配的文本块和来源信息').
- [x] 5.4 Add `'rag_search'` to the `researcher` preset's `tools` array in `AGENT_TOOL_PRESETS` (line 91).
- [x] 5.5 Verify `normalizeAgentToolNames` will include `rag_search` automatically (it uses `AVAILABLE_AGENT_TOOLS` set).

## 6. Frontend: remove RAG toggle from message input

- [x] 6.1 Delete the RAG toggle button (BookOpen icon) from `src/components/message-input.tsx` (line 1124-1143).
- [x] 6.2 Delete `toggleRagMode` function and `ragEnabled` variable from `message-input.tsx` (line 850-864).
- [x] 6.3 Delete `setRagMode` function from `src/lib/api.ts` (line 390-398).
- [x] 6.4 Remove `ragEnabled` field from `src/db/schema.ts` (line 94).

## 7. Spec sync

- [ ] 7.1 Verify `openspec validate move-rag-to-agent-tool` passes.
- [ ] 7.2 Update `specs/07-tools.md` to add `rag_search` tool entry (signature, params, behavior) and mark the conversation-level injection as deprecated.
- [ ] 7.3 Update `specs/10-agent-builder.md` to reflect 6 UI-selectable tools and `rag_search` in the researcher preset.

## 8. Verification

- [ ] 8.1 `ruff check .` passes on backend.
- [ ] 8.2 `pnpm typecheck` and `pnpm lint` pass on frontend.
- [ ] 8.3 Manual test: create/edit a custom agent, verify `rag_search` appears as a selectable tool checkbox.
- [ ] 8.4 Manual test: select researcher preset, verify `rag_search` is auto-selected.
- [ ] 8.5 Manual test: run an agent with `rag_search` enabled, verify the tool is available and returns results.
- [ ] 8.6 Manual test: verify the message input toolbar no longer has a RAG toggle button.
- [ ] 8.7 `openspec archive move-rag-to-agent-tool` after acceptance.
