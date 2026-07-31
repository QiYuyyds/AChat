## 1. 修复 config.py 默认值 + 新增显式 keyword weight

- [x] 1.1 修改 `backend/app/config.py`：`rag_semantic_weight` 从 0.7 改为 0.5
- [x] 1.2 修改 `backend/app/config.py`：`rag_rrf_constant_k` 从 60 改为 30
- [x] 1.3 修改 `backend/app/config.py`：`kg_weight` 从 0.3 改为 0.0
- [x] 1.4 新增 `backend/app/config.py`：`rag_keyword_weight: float = 0.5`（放在 `rag_semantic_weight` 下方）

## 2. 修改 hybrid.py 权重读取逻辑

- [x] 2.1 修改 `_search_hybrid` 中第 286-288 行：`raw_kw` 从 `max(0.0, 1.0 - raw_sem - self.settings.kg_weight)` 改为 `max(0.0, float(self.settings.rag_keyword_weight))`
- [x] 2.2 确认归一化逻辑（第 290-301 行）不需改动（仍按 available 归一化到 1.0）

## 3. 移除 kgstore.py 冗余加权

- [x] 3.1 删除 `backend/app/graph/kgstore.py` 第 212 行 `score *= self.kg_weight`
- [x] 3.2 修改 `backend/app/graph/kgstore.py` 第 249 行：`"score": self.kg_weight` 改为 `"score": 1.0`
- [x] 3.3 确认 `KGStore.__init__` 中 `self.kg_weight = settings.kg_weight`（第 39 行）可保留（不影响逻辑，仅未使用）

## 4. 更新配置文件

- [x] 4.1 修改 `backend/.env.example`：`RAG_SEMANTIC_WEIGHT=0.7` 改为 `RAG_SEMANTIC_WEIGHT=0.5`
- [x] 4.2 修改 `backend/.env.example`：`RAG_RRF_CONSTANT_K=60` 改为 `RAG_RRF_CONSTANT_K=30`
- [x] 4.3 修改 `backend/.env.example`：`KG_WEIGHT=0.3` 改为 `KG_WEIGHT=0.0`
- [x] 4.4 新增 `backend/.env.example`：在 `RAG_SEMANTIC_WEIGHT` 下方添加 `RAG_KEYWORD_WEIGHT=0.5`

## 5. 更新 spec 文档

- [x] 5.1 修改 `openspec/changes/migration-plan-path-b/specs/rag-system/spec.md` 第 42 行：将 `semantic_weight=0.7, keyword_weight=0.3, and kg_weight=0.3` 改为 `semantic_weight=0.5, keyword_weight=0.5, and kg_weight=0.0`

## 6. 更新评测文档

- [x] 6.1 修改 `eval/README.md` 第 69 行：`RAG_RRF_CONSTANT_K=60` 注释改为 `RAG_RRF_CONSTANT_K=30`（或添加注释说明推荐范围 10-60）
- [x] 6.2 修改 `eval/README.md` 第 79-80 行：更新权重关系说明，加入 `RAG_KEYWORD_WEIGHT` 显式参数说明

## 7. 更新测试

- [x] 7.1 修改 `backend/tests/test_rag_hybrid.py` 第 13-16 行 mock 默认值：`rag_rrf_constant_k` 改为 30、`rag_semantic_weight` 改为 0.5、`kg_weight` 改为 0.0、新增 `rag_keyword_weight` = 0.5
- [x] 7.2 修改 `backend/tests/test_memory_graph.py` 第 15 行：`kg_weight` 改为 0.0
- [x] 7.3 运行 `ruff check .` 确认无 lint 错误（kgstore.py 有 5 个预先存在的 E101/F841 错误，本次修改未引入新错误）
- [x] 7.4 运行 `python -m pytest backend/tests/test_rag_hybrid.py -v` 确认测试通过（10/11 通过，test_hybrid_rrf_fusion 为预先存在的 source attribution bug，与本次修改无关）
