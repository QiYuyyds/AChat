"""Span name English-Chinese bilingual mapping table.

Engineers write the English key (e.g. ``agent.run``) when instrumenting;
the final span name shown in Phoenix UI is ``agent.run · 代理运行``.
"""

SPAN_NAMES: dict[str, str] = {
    "agent.run": "agent.run · 代理运行",
    "agent.build_context": "agent.build_context · 上下文组装",
    "agent.finalize": "agent.finalize · 运行收尾",
    "prompt.assemble": "prompt.assemble · 提示词组装",
    "memory.recall": "memory.recall · 记忆召回",
    "memory.ltm.query": "memory.ltm.query · 长期记忆查询",
    "memory.stm.get": "memory.stm.get · 短期记忆读取",
    "rag.search": "rag.search · 知识检索",
    "rag.ingest": "rag.ingest · 知识入库",
    "rag.query_rewrite": "rag.query_rewrite · 查询改写",
    "rag.milvus_search": "rag.milvus_search · 向量检索",
    "rag.es_search": "rag.es_search · 全文检索",
    "rag.kg_search": "rag.kg_search · 图谱检索",
    "rag.rrf_fuse": "rag.rrf_fuse · 结果融合",
    "adapter.stream": "adapter.stream · 模型推理",
    "llm.generate": "llm.generate · LLM生成",
    "tool.call": "tool.call · 工具调用",
    "tool.dispatch": "tool.dispatch · 任务派发",
    "eval.score": "eval.score · 评测打分",
    "eval.judge": "eval.judge · LLM评判",
    "dag.execute": "dag.execute · DAG执行",
    "dag.wave": "dag.wave · 波次调度",
    "dag.node": "dag.node · 节点执行",
}


def resolve_span_name(span_key: str, suffix: str | None = None) -> str:
    """Look up the bilingual span name for *span_key*.

    Falls back to the raw key if not registered.  Supports an optional
    dynamic *suffix* appended in parentheses.
    """
    name = SPAN_NAMES.get(span_key, span_key)
    if suffix:
        name = f"{name} ({suffix})"
    return name
