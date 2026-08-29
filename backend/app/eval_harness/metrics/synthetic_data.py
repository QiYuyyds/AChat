"""Synthetic data generation — documents → Goldens → dataset items (D6).

Golden = a synthetic eval case (input / expected_output / source context).
Long text is chunked by character length (no tokenizer dependency). Goldens
convert to dataset items with llm_generated provenance and default
answer_relevancy + faithfulness metric graders (threshold 0.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eval_harness.dataset.models import EvalDatasetItem, SourceType, now_ms
from eval_harness.dataset.sources.llm_generator import LLMFn, _extract_json_array
from eval_harness.dataset.sources.manual import DatasetImportError
from eval_harness.metrics.llm_judge import require_llm_fn

# 分块大小 (字符; 按长度估算, 不做 tokenizer 依赖) 与重叠
_CHUNK_SIZE_CHARS = 2000
_CHUNK_OVERLAP_CHARS = 200

_SYNTH_SYSTEM_PROMPT = "You are an evaluation dataset designer. You output only valid JSON."

_SYNTH_PROMPT = """根据以下文档, 生成 {count} 个高质量评测问题。

文档内容:
{context}

对每个问题, 提供:
- "input": 用户问题 (应能从文档中回答)
- "expected_output": 标准答案 (基于文档)

要求:
- 问题类型多样 (事实性/推理性/比较性)
- 答案必须能从文档中直接推导
- 避免模糊或主观的问题

以 JSON 数组格式返回，不要输出数组以外的任何内容。"""


@dataclass
class Golden:
    """评测黄金标准 — 单个合成测试用例"""

    input: str  # 问题
    expected_output: str  # 期望回答
    context: list[str] = field(default_factory=list)  # 来源文档
    source: str = ""  # 来源引用


class SyntheticDataGenerator:
    """合成数据生成器 — 从文档自动生成评测用例"""

    def __init__(self, llm_fn: LLMFn | None = None):
        self.llm_fn = llm_fn

    async def generate_from_docs(
        self,
        documents: list[str],
        count_per_doc: int = 3,
        llm_fn: LLMFn | None = None,
    ) -> list[Golden]:
        """从文档列表生成评测用例 (每文档 count_per_doc 个)"""
        fn = require_llm_fn(llm_fn or self.llm_fn)
        goldens: list[Golden] = []
        for idx, doc in enumerate(documents):
            if not str(doc).strip():
                continue
            raw = await fn(
                _SYNTH_SYSTEM_PROMPT,
                _SYNTH_PROMPT.format(context=doc, count=count_per_doc),
            )
            parsed = _extract_json_array(raw)
            if parsed is None:
                raise DatasetImportError(
                    f"Synthetic generation: document #{idx} judge response is not "
                    f"a parsable JSON array (got: {raw[:120]!r}...)"
                )
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                question = str(entry.get("input", "")).strip()
                answer = str(entry.get("expected_output", "")).strip()
                if not question or not answer:
                    continue
                goldens.append(Golden(
                    input=question,
                    expected_output=answer,
                    context=[str(doc)],
                    source=f"doc[{idx}]",
                ))
        return goldens

    async def generate_from_text(
        self,
        text: str,
        count: int = 5,
        llm_fn: LLMFn | None = None,
    ) -> list[Golden]:
        """从单段长文本生成评测用例 (先分块再逐块生成)"""
        fn = llm_fn or self.llm_fn
        chunks = self.chunk_text(text)
        if not chunks:
            return []
        per_chunk = max(1, count // len(chunks))
        remainder = count - per_chunk * len(chunks)

        goldens: list[Golden] = []
        for i, chunk in enumerate(chunks):
            take = per_chunk + (1 if i < remainder else 0)
            if take <= 0:
                continue
            goldens.extend(await self.generate_from_docs([chunk], take, llm_fn=fn))
        return goldens

    @staticmethod
    def chunk_text(
        text: str,
        max_chars: int = _CHUNK_SIZE_CHARS,
        overlap: int = _CHUNK_OVERLAP_CHARS,
    ) -> list[str]:
        """按字符长度分块 (段落边界优先, 带重叠)"""
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append(current)
                # 保留尾部重叠, 维持跨块上下文
                current = current[-overlap:] + "\n\n" + para if overlap else para
            else:
                current = f"{current}\n\n{para}" if current else para
            # 单段超长: 硬切
            while len(current) > max_chars:
                chunks.append(current[:max_chars])
                current = current[max_chars - overlap:] if overlap else ""
        if current.strip():
            chunks.append(current)
        return chunks

    def to_dataset_items(
        self,
        goldens: list[Golden],
        threshold: float = 0.7,
    ) -> list[EvalDatasetItem]:
        """
        Golden → 数据集条目 (D6)。

        默认 graders: answer_relevancy + faithfulness 指标 (threshold 0.7),
        source_type=llm_generated, source_ref=来源文档引用。
        """
        from eval_harness.dataset.models import make_grader_config

        # answer_relevancy 对所有条目相同; faithfulness 需要按条目注入
        # Golden context (grader config.context) 才能在流水线中评估忠实度
        relevancy_grader = make_grader_config(
            "metric",
            name="answer_relevancy",
            metric_name="answer_relevancy",
            threshold=threshold,
        )
        items = []
        for i, g in enumerate(goldens):
            faithfulness_grader = make_grader_config(
                "metric",
                name="faithfulness",
                metric_name="faithfulness",
                threshold=threshold,
                context=g.context,
            )
            items.append(EvalDatasetItem(
                id=f"synthetic_{i:04d}",
                prompt=g.input,
                description=f"Synthetic: {g.input[:60]}",
                graders=[relevancy_grader, faithfulness_grader],
                env={},
                metadata={
                    "capabilities": ["rag"],
                    "context": g.context,
                    "expected_output": g.expected_output,
                },
                source_type=SourceType.LLM_GENERATED,
                source_ref=g.source,
                created_at=now_ms(),
            ))
        return items

    async def generate_dataset_items(
        self,
        documents: list[str],
        count_per_doc: int = 3,
        threshold: float = 0.7,
        llm_fn: LLMFn | None = None,
    ) -> list[EvalDatasetItem]:
        """文档 → Golden → 数据集条目 (便捷链路)"""
        goldens = await self.generate_from_docs(documents, count_per_doc, llm_fn)
        return self.to_dataset_items(goldens, threshold=threshold)
