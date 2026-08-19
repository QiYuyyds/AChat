## 1. Filter bar restructure

- [x] 1.1 In `long-term-memory-panel.tsx`, remove the type `<select>`, the「筛选」Filter label, and the Agent ID input; drop `filterAgent` state and stop passing `agentId` in `fetchMemoryFiles`
- [x] 1.2 Keep search + 刷新 + 手动精炼 on the first toolbar row

## 2. Type chips

- [x] 2.1 Add a second row of single-select chips: 全部 / 经验 / 知识 / 日常 (values `all` | `procedure` | `wiki` | `daily`), no count badges
- [x] 2.2 Wire chip click → `setFilterBucket` + exit search mode (`setSearchMode(false)`), relying on existing `load` deps to refetch
- [x] 2.3 Style chips with shared `BUCKET_CONFIG` colors (neutral for 全部; procedure blue / wiki emerald / daily amber selected states aligned with interest-topic pills)

## 3. Verify

- [x] 3.1 Manual check: default 全部; switch types reloads list; chip colors match cards; search Enter then click chip exits search and filters; clear search restores chip filter; no dropdown / Agent ID in bar
- [x] 3.2 Run `pnpm typecheck` (or project equivalent) for the touched frontend file
